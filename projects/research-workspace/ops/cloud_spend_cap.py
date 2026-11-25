"""Plan or create a reviewed, project-scoped Cloud Run spend cap.

Pass a private JSON config with --config; only --apply may create a budget.
Credentials come solely from RESEARCH_OPERATOR_GOOGLE_ACCESS_TOKEN. Existing
budgets are never patched, lifted, rearmed, or deleted. After uncertain creation,
rerun without --apply to reconcile before deciding on any further action.
The dedicated project supplies API quota via X-Goog-User-Project; the operator
must have serviceusage.services.use on that project.

This covers Cloud Run only, uses gross costs, resets each calendar month, and
can overshoot because enforcement is delayed. Fixed and other-provider costs
continue. Keep independent finite workload limits and an all-provider ledger.
API contract: https://docs.cloud.google.com/billing/docs/reference/budget/rest/v1/billingAccounts.budgets
"""
import argparse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re

import httpx


SERVICE = 'services/152E-C115-5142'
BILLING = 'https://cloudbilling.googleapis.com/v1/'
BUDGETS = 'https://billingbudgets.googleapis.com/v1/'
PROJECTS = 'https://cloudresourcemanager.googleapis.com/v3/'
MAX_PAGES = 20


class CapError(Exception):
    """A fixed, credential-free failure category safe for operator output."""


def money(value):
    if isinstance(value, bool):
        raise CapError('invalid_money')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise CapError('invalid_money') from None
    if not number.is_finite() or number <= 0 or number >= 10 ** 12 or number * 100 != (number * 100).to_integral_value():
        raise CapError('invalid_money')
    return number


@dataclass(frozen=True)
class Config:
    project_id: str
    billing_account: str
    currency_code: str
    amount: Decimal
    usd_equivalent: Decimal
    monthly_total_usd: Decimal
    quote_date: str
    evidence_reference: str

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict) or set(values) != set(cls.__dataclass_fields__):
            raise CapError('invalid_config_fields')
        if not isinstance(values['project_id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', values['project_id']):
            raise CapError('invalid_project_id')
        if not isinstance(values['billing_account'], str) or not re.fullmatch(r'[A-Z0-9]{6}(?:-[A-Z0-9]{6}){2}', values['billing_account']):
            raise CapError('invalid_billing_account')
        if values['currency_code'] not in ('HKD', 'USD'):
            raise CapError('unsupported_currency')
        parsed = dict(values)
        for key in ('amount', 'usd_equivalent', 'monthly_total_usd'):
            parsed[key] = money(values[key])
        if not parsed['usd_equivalent'] <= parsed['monthly_total_usd'] < 5:
            raise CapError('outside_monthly_ceiling')
        if parsed['currency_code'] == 'USD' and parsed['amount'] != parsed['usd_equivalent']:
            raise CapError('usd_amount_mismatch')
        try:
            if date.fromisoformat(values['quote_date']).isoformat() != values['quote_date'] or date.fromisoformat(values['quote_date']) > date.today():
                raise ValueError
        except (ValueError, TypeError):
            raise CapError('invalid_quote_date') from None
        if not isinstance(values['evidence_reference'], str) or not 4 <= len(values['evidence_reference'].strip()) <= 500:
            raise CapError('missing_review_evidence')
        return cls(**parsed)

    @property
    def display_name(self):
        return f'research-{self.project_id}-cloud-run-cap'

    @property
    def account_name(self):
        return 'billingAccounts/' + self.billing_account

    def budget(self, project_name):
        cents = int(self.amount * 100)
        return {
            'displayName': self.display_name,
            'budgetFilter': {'projects': [project_name], 'services': [SERVICE],
                             'calendarPeriod': 'MONTH', 'creditTypesTreatment': 'EXCLUDE_ALL_CREDITS'},
            'amount': {'specifiedAmount': {'currencyCode': self.currency_code,
                       'units': str(cents // 100), 'nanos': (cents % 100) * 10_000_000}},
            'thresholdRules': [{'thresholdPercent': fraction, 'spendBasis': 'CURRENT_SPEND'}
                               for fraction in (0.5, 0.8, 1.0)],
            'notificationsRule': {'enableProjectLevelRecipients': True, 'disableDefaultIamRecipients': False},
            'ownershipScope': 'ALL_USERS',
            'spendCap': {'inputState': 'CONFIGURED'},
        }


def request(client, token, quota_project, method, url, **kwargs):
    response = client.request(method, url, headers={'Authorization': 'Bearer ' + token,
                              'X-Goog-User-Project': quota_project},
                              timeout=10, follow_redirects=False, **kwargs)
    if not 200 <= response.status_code < 300:
        raise CapError('google_http_' + str(response.status_code))
    if len(response.content) > 2_000_000:
        raise CapError('oversized_google_response')
    try:
        result = response.json()
    except ValueError:
        raise CapError('invalid_google_response') from None
    if not isinstance(result, dict):
        raise CapError('invalid_google_response')
    return result


def budget_url(config, name):
    if not isinstance(name, str) or not re.fullmatch(re.escape(config.account_name) + r'/budgets/[A-Za-z0-9_-]{1,128}', name):
        raise CapError('invalid_budget_resource')
    return BUDGETS + name


def verify_budget(actual, expected):
    """Verify server state, not an echo of the mutation's inputState.

    Live GET responses omit inputState and false reconciling. ProtoJSON omits
    default scalar values; requiring the create request's shape rejected a valid
    cap. Only an explicit configured/enforced output permits those omissions.
    https://protobuf.dev/programming-guides/json/#presence-and-default-values
    """
    try:
        filters = actual['budgetFilter']
        wanted = expected['budgetFilter']
        if any(filters.get(key) != value for key, value in wanted.items()):
            raise CapError('budget_scope_mismatch')
        if any(value for key, value in filters.items() if key not in wanted):
            raise CapError('budget_scope_mismatch')
        amount = actual['amount']
        value = amount['specifiedAmount']
        expected_value = expected['amount']['specifiedAmount']
        if (value.get('currencyCode') != expected_value['currencyCode'] or
                str(value.get('units', '0')) != expected_value['units'] or
                value.get('nanos', 0) != expected_value['nanos'] or amount.get('lastPeriodAmount') is not None):
            raise CapError('budget_amount_mismatch')
        rules = actual['thresholdRules']
        if (len(rules) != 3 or {Decimal(str(rule['thresholdPercent'])) for rule in rules} != {Decimal('.5'), Decimal('.8'), Decimal('1')} or
                any(rule.get('spendBasis', 'BASIS_UNSPECIFIED') not in ('CURRENT_SPEND', 'BASIS_UNSPECIFIED') for rule in rules)):
            raise CapError('budget_threshold_mismatch')
        notifications = actual['notificationsRule']
        if (notifications.get('enableProjectLevelRecipients') is not True or
                notifications.get('disableDefaultIamRecipients', False) is not False or
                any(value for key, value in notifications.items()
                    if key not in ('enableProjectLevelRecipients', 'disableDefaultIamRecipients'))):
            raise CapError('budget_notification_mismatch')
        if actual.get('ownershipScope', 'OWNERSHIP_SCOPE_UNSPECIFIED') not in ('OWNERSHIP_SCOPE_UNSPECIFIED', 'ALL_USERS'):
            raise CapError('budget_ownership_mismatch')
        cap = actual['spendCap']
        if not isinstance(cap, dict):
            raise CapError('cap_state_unverified')
        if ('inputState' in cap and cap['inputState'] != 'CONFIGURED') or cap.get('outputState') == 'AWAITING_NEXT_PERIOD':
            raise CapError('cap_not_configured')
        reconciling = cap.get('reconciling', False)
        if type(reconciling) is not bool:
            raise CapError('cap_state_unverified')
        if cap['outputState'] not in ('CONFIGURED', 'ENFORCED'):
            raise CapError('cap_state_unverified')
        return {
            'status': 'pending' if reconciling else cap['outputState'].lower(),
            'reconciling': reconciling,
            'reconciliation_evidence': 'explicit' if 'reconciling' in cap else 'protojson_default_false',
        }
    except (KeyError, TypeError, InvalidOperation):
        raise CapError('invalid_budget_response') from None


def run(config, client, token, *, apply=False):
    if not isinstance(token, str) or not token.strip() or any(char.isspace() for char in token):
        raise CapError('missing_operator_access_token')
    account = request(client, token, config.project_id, 'GET', BILLING + config.account_name)
    if account.get('name') != config.account_name or account.get('open') is not True or account.get('masterBillingAccount'):
        raise CapError('billing_account_not_eligible')
    if account.get('currencyCode') != config.currency_code:
        raise CapError('billing_currency_unverified')
    link = request(client, token, config.project_id, 'GET', BILLING + 'projects/' + config.project_id + '/billingInfo')
    if (link.get('name') != 'projects/' + config.project_id + '/billingInfo' or
            link.get('projectId') != config.project_id or link.get('billingAccountName') != config.account_name or
            link.get('billingEnabled') is not True):
        raise CapError('project_billing_link_unverified')
    project = request(client, token, config.project_id, 'GET', PROJECTS + 'projects/' + config.project_id)
    if (project.get('projectId') != config.project_id or project.get('state') != 'ACTIVE' or
            not re.fullmatch(r'projects/[0-9]{1,20}', str(project.get('name', '')))):
        raise CapError('project_identity_unverified')
    expected = config.budget(project['name'])
    collection = BUDGETS + config.account_name + '/budgets'
    matches, page_token, seen = [], None, set()
    for _ in range(MAX_PAGES):
        params = {'pageSize': 100}
        if page_token:
            params['pageToken'] = page_token
        page = request(client, token, config.project_id, 'GET', collection, params=params)
        budgets = page.get('budgets', [])
        if not isinstance(budgets, list) or len(budgets) > 100 or any(not isinstance(item, dict) for item in budgets):
            raise CapError('invalid_budget_listing')
        matches.extend(item for item in budgets if item.get('displayName') == config.display_name)
        page_token = page.get('nextPageToken')
        if not page_token:
            break
        if not isinstance(page_token, str) or len(page_token) > 4096 or page_token in seen:
            raise CapError('invalid_budget_pagination')
        seen.add(page_token)
    else:
        raise CapError('budget_listing_limit')
    if len(matches) > 1:
        raise CapError('duplicate_budget_name')
    report = {'status': 'planned', 'ready_to_run': False, 'created': False,
              'currency_code': config.currency_code, 'amount': str(config.amount),
              'usd_equivalent': str(config.usd_equivalent), 'monthly_total_usd': str(config.monthly_total_usd)}
    if matches:
        resource_name = matches[0].get('name')
        actual = request(client, token, config.project_id, 'GET', budget_url(config, resource_name))
    elif not apply:
        return report
    else:
        try:
            created = request(client, token, config.project_id, 'POST', collection, json=expected)
        except (httpx.HTTPError, CapError) as error:
            category = str(error) if isinstance(error, CapError) else type(error).__name__
            return {**report, 'status': 'creation_uncertain', 'category': category,
                    'next_step': 'rerun_without_apply'}
        report['created'] = True
        resource_name = created.get('name')
        actual = request(client, token, config.project_id, 'GET', budget_url(config, resource_name))
    if actual.get('displayName') != config.display_name or actual.get('name') != resource_name:
        raise CapError('budget_name_mismatch')
    verified = verify_budget(actual, expected)
    return {**report, **verified, 'ready_to_run': verified['status'] == 'configured'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Create only if the dedicated cap is absent')
    args = parser.parse_args(argv)
    try:
        config = Config.from_dict(json.loads(args.config.read_text(encoding='utf-8-sig'), parse_float=Decimal))
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
            report = run(config, client, os.environ.get('RESEARCH_OPERATOR_GOOGLE_ACCESS_TOKEN'), apply=args.apply)
        print(json.dumps(report))
        return 2 if report['status'] in ('pending', 'creation_uncertain', 'enforced') else 0
    except Exception as error:
        category = str(error) if isinstance(error, CapError) else type(error).__name__
        print(json.dumps({'status': 'failed', 'category': category}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
