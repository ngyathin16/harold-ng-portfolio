"""Spend-cap creation must converge without lifting or replacing existing caps."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from ops import cloud_spend_cap as cap


PROJECT = 'research-budget-fixture'
ACCOUNT = '000000-000000-000000'
ACCOUNT_NAME = 'billingAccounts/' + ACCOUNT
BUDGET_NAME = ACCOUNT_NAME + '/budgets/fixture-cap'
DISPLAY_NAME = 'research-' + PROJECT + '-cloud-run-cap'
TOKEN = 'test-token-must-never-appear-in-reports'


def configuration(**changes):
    values = {
        'project_id': PROJECT, 'billing_account': ACCOUNT,
        'currency_code': 'HKD', 'amount': 8, 'usd_equivalent': 1.04,
        'monthly_total_usd': 4.99,
        'quote_date': datetime.now(timezone.utc).date().isoformat(),
        'evidence_reference': 'fixture: reviewed offline, not authorization',
    }
    values.update(changes)
    return cap.Config.from_dict(values)


def budget(state='CONFIGURED', reconciling=False):
    return {
        'name': BUDGET_NAME,
        'displayName': DISPLAY_NAME,
        'budgetFilter': {
            'projects': ['projects/123456789012'],
            'services': ['services/152E-C115-5142'],
            'calendarPeriod': 'MONTH',
            'creditTypesTreatment': 'EXCLUDE_ALL_CREDITS',
        },
        'amount': {'specifiedAmount': {'currencyCode': 'HKD', 'units': '8', 'nanos': 0}},
        'thresholdRules': [
            {'thresholdPercent': value, 'spendBasis': 'CURRENT_SPEND'}
            for value in (0.5, 0.8, 1.0)
        ],
        'notificationsRule': {
            'enableProjectLevelRecipients': True,
            'disableDefaultIamRecipients': False,
        },
        'ownershipScope': 'ALL_USERS',
        'spendCap': {
            'inputState': 'CONFIGURED', 'outputState': state,
            'reconciling': reconciling,
        },
    }


def protojson_budget(state='CONFIGURED'):
    """Sanitized shape observed from Google GET, with default fields omitted."""
    item = budget(state)
    item['spendCap'] = {'outputState': state}
    del item['amount']['specifiedAmount']['nanos']
    del item['ownershipScope']
    del item['notificationsRule']['disableDefaultIamRecipients']
    return item


class Remote:
    """Stateful HTTP boundary: mutation responses can be lost after persistence."""

    def __init__(self, budgets=()):
        self.budgets = deepcopy(list(budgets))
        self.account = {'name': ACCOUNT_NAME, 'open': True, 'currencyCode': 'HKD'}
        self.link = {
            'name': 'projects/' + PROJECT + '/billingInfo', 'projectId': PROJECT,
            'billingAccountName': ACCOUNT_NAME, 'billingEnabled': True,
        }
        self.project = {'name': 'projects/123456789012', 'projectId': PROJECT, 'state': 'ACTIVE'}
        self.requests = []
        self.create_failure = None
        self.created = None
        self.readback = None
        self.account_redirect = False
        self.pages = None
        self.endless_pages = False
        self.page_count = 0

    def __call__(self, request):
        self.requests.append(request)
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise AssertionError('Operator token was not sent as an authorization header')
        if request.headers.get('X-Goog-User-Project') != PROJECT:
            raise AssertionError('Every Google request must use the dedicated quota project')
        if request.url.scheme != 'https':
            raise AssertionError('An insecure request was attempted')
        host, path = request.url.host, request.url.path
        if request.method == 'GET' and host == 'cloudbilling.googleapis.com':
            if path == '/v1/' + ACCOUNT_NAME:
                if self.account_redirect:
                    return httpx.Response(302, headers={'Location': 'https://unexpected.example/private'})
                return httpx.Response(200, json=self.account)
            if path == '/v1/projects/' + PROJECT + '/billingInfo':
                return httpx.Response(200, json=self.link)
        if request.method == 'GET' and host == 'cloudresourcemanager.googleapis.com':
            if path == '/v3/projects/' + PROJECT:
                return httpx.Response(200, json=self.project)
        if host == 'billingbudgets.googleapis.com':
            if path == '/v1/' + ACCOUNT_NAME + '/budgets':
                if request.method == 'GET':
                    self.page_count += 1
                    if self.endless_pages:
                        return httpx.Response(200, json={
                            'budgets': [], 'nextPageToken': 'page-' + str(self.page_count)})
                    if self.pages is not None:
                        return httpx.Response(200, json=self.pages[self.page_count - 1])
                    return httpx.Response(200, json={'budgets': self.budgets})
                if request.method == 'POST':
                    self.created = json.loads(request.content)
                    saved = deepcopy(self.created)
                    saved['name'] = BUDGET_NAME
                    saved['spendCap'].update(outputState='CONFIGURED', reconciling=False)
                    self.budgets.append(saved)
                    if self.create_failure == 'timeout':
                        raise httpx.ReadTimeout('private response may contain ' + TOKEN, request=request)
                    if self.create_failure == 'http':
                        return httpx.Response(503, json={'error': {'message': TOKEN}})
                    return httpx.Response(200, json=saved)
            if request.method == 'GET' and path == '/v1/' + BUDGET_NAME:
                return httpx.Response(200, json=self.readback or self.budgets[0])
        raise AssertionError('Unexpected HTTP operation: ' + request.method + ' ' + str(request.url))

    def invoke(self, apply=False, config=None):
        with httpx.Client(transport=httpx.MockTransport(self), trust_env=False) as client:
            return cap.run(config or configuration(), client, TOKEN, apply=apply)

    @property
    def mutations(self):
        return [request for request in self.requests if request.method != 'GET']


class ConfigurationTests(unittest.TestCase):
    def test_hkd_amount_above_five_is_allowed_inside_usd_ceiling(self):
        self.assertEqual(configuration().display_name, DISPLAY_NAME)

    def test_invalid_configuration_is_rejected_before_http(self):
        cases = (
            {'project_id': '../other-project'},
            {'billing_account': '000000-000000-000000/budgets/other'},
            {'currency_code': 'XYZ'}, {'currency_code': 'hkd'},
            {'amount': 0}, {'amount': -1}, {'amount': 8.001},
            {'amount': float('nan')}, {'amount': float('inf')},
            {'usd_equivalent': 0}, {'usd_equivalent': 5},
            {'usd_equivalent': 4.5, 'monthly_total_usd': 4},
            {'monthly_total_usd': 5}, {'monthly_total_usd': 4.999},
            {'currency_code': 'USD', 'amount': 2, 'usd_equivalent': 1.04},
            {'quote_date': 'not-a-date'}, {'evidence_reference': ''},
            {'access_token': TOKEN},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(cap.CapError):
                configuration(**changes)


class SpendCapTests(unittest.TestCase):
    def test_dedicated_quota_project_covers_paginated_create_and_readback(self):
        remote = Remote()
        remote.pages = [{'budgets': [], 'nextPageToken': 'second'}, {'budgets': []}]
        result = remote.invoke(apply=True)
        self.assertEqual(result['status'], 'configured')
        self.assertEqual(remote.page_count, 2)
        self.assertEqual(len(remote.mutations), 1)
        self.assertEqual(remote.requests[-1].url.path, '/v1/' + BUDGET_NAME)
        self.assertTrue(all(request.headers.get('X-Goog-User-Project') == PROJECT
                            for request in remote.requests))

    def test_default_plan_reads_live_identity_and_never_mutates(self):
        remote = Remote()
        result = remote.invoke()
        self.assertEqual(result['status'], 'planned')
        self.assertFalse(remote.mutations)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual({request.url.host for request in remote.requests}, {
            'cloudbilling.googleapis.com', 'cloudresourcemanager.googleapis.com',
            'billingbudgets.googleapis.com'})

    def test_apply_creates_real_single_service_spend_cap_and_reads_it_back(self):
        remote = Remote()
        result = remote.invoke(apply=True)
        self.assertEqual(result['status'], 'configured')
        self.assertEqual([request.method for request in remote.mutations], ['POST'])
        body = remote.created
        expected = budget()
        for field in ('displayName', 'budgetFilter', 'amount', 'thresholdRules'):
            self.assertEqual(body[field], expected[field])
        self.assertEqual(body['spendCap'], {'inputState': 'CONFIGURED'})
        self.assertIs(body['notificationsRule']['enableProjectLevelRecipients'], True)
        self.assertIs(body['notificationsRule'].get('disableDefaultIamRecipients', False), False)
        self.assertFalse(body['notificationsRule'].get('pubsubTopic'))
        self.assertFalse(body['notificationsRule'].get('monitoringNotificationChannels'))
        self.assertEqual(remote.requests[-1].method, 'GET')
        self.assertEqual(remote.requests[-1].url.path, '/v1/' + BUDGET_NAME)
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_existing_configured_or_enforced_cap_is_never_rearmed(self):
        for state in ('CONFIGURED', 'ENFORCED'):
            with self.subTest(state=state):
                remote = Remote([budget(state)])
                result = remote.invoke(apply=True)
                self.assertEqual(result['status'], state.lower())
                self.assertIs(result['ready_to_run'], state == 'CONFIGURED')
                self.assertIs(result['reconciling'], False)
                self.assertEqual(result['reconciliation_evidence'], 'explicit')
                self.assertFalse(remote.mutations)

    def test_protojson_omissions_accept_explicit_active_or_enforced_output(self):
        for state in ('CONFIGURED', 'ENFORCED'):
            with self.subTest(state=state):
                remote = Remote([protojson_budget(state)])
                result = remote.invoke(apply=True)
                self.assertEqual(result['status'], state.lower())
                self.assertIs(result['ready_to_run'], state == 'CONFIGURED')
                self.assertIs(result['reconciling'], False)
                self.assertEqual(result['reconciliation_evidence'], 'protojson_default_false')
                self.assertFalse(remote.mutations)

    def test_created_cap_accepts_protojson_readback_without_creating_again(self):
        remote = Remote()
        remote.readback = protojson_budget()
        result = remote.invoke(apply=True)
        self.assertEqual(result['status'], 'configured')
        self.assertIs(result['ready_to_run'], True)
        self.assertEqual(result['reconciliation_evidence'], 'protojson_default_false')
        self.assertEqual(remote.invoke(apply=True)['status'], 'configured')
        self.assertEqual([request.method for request in remote.mutations], ['POST'])

    def test_reconciliation_reports_pending_without_mutation(self):
        remote = Remote([budget(reconciling=True)])
        result = remote.invoke(apply=True)
        self.assertEqual(result['status'], 'pending')
        self.assertIs(result['ready_to_run'], False)
        self.assertIs(result['reconciling'], True)
        self.assertEqual(result['reconciliation_evidence'], 'explicit')
        self.assertFalse(remote.mutations)

    def test_reconciliation_cannot_certify_unspecified_output_state(self):
        remote = Remote([budget(state='STATE_UNSPECIFIED', reconciling=True)])
        with self.assertRaises(cap.CapError):
            remote.invoke(apply=True)
        self.assertFalse(remote.mutations)

    def test_creation_is_not_ready_until_readback_reconciliation_finishes(self):
        remote = Remote()
        remote.readback = budget(reconciling=True)
        result = remote.invoke(apply=True)
        self.assertEqual(result['status'], 'pending')
        self.assertIs(result['ready_to_run'], False)
        self.assertEqual([request.method for request in remote.mutations], ['POST'])

    def test_untrusted_budget_resource_cannot_change_request_destination(self):
        for name in ('https://unexpected.example/private', 'billingAccounts/OTHER/budgets/other'):
            with self.subTest(name=name):
                item = budget()
                item['name'] = name
                remote = Remote([item])
                with self.assertRaises(cap.CapError):
                    remote.invoke(apply=True)
                self.assertFalse(remote.mutations)
                self.assertTrue(all(request.url.host.endswith('.googleapis.com')
                                    for request in remote.requests))

    def test_misdirected_readback_cannot_certify_matching_cap_contents(self):
        remote = Remote([budget()])
        remote.readback = budget()
        remote.readback['name'] = ACCOUNT_NAME + '/budgets/different-cap'
        with self.assertRaises(cap.CapError):
            remote.invoke(apply=True)
        self.assertFalse(remote.mutations)

    def test_redirect_is_not_followed_even_if_client_enables_redirects(self):
        remote = Remote()
        remote.account_redirect = True
        with httpx.Client(transport=httpx.MockTransport(remote), follow_redirects=True) as client:
            with self.assertRaises(cap.CapError):
                cap.run(configuration(), client, TOKEN, apply=True)
        self.assertEqual(len(remote.requests), 1)
        self.assertFalse(remote.mutations)

    def test_explicit_invalid_state_fields_cannot_use_protojson_defaults(self):
        invalid_fields = (
            ('inputState', None), ('inputState', False), ('inputState', 0),
            ('inputState', 'STATE_UNSPECIFIED'), ('inputState', 'ENFORCED'),
            ('reconciling', None), ('reconciling', 'false'), ('reconciling', 'true'),
            ('reconciling', 0), ('reconciling', 1),
            ('outputState', None), ('outputState', False), ('outputState', 0),
            ('outputState', 'unknown'), ('outputState', 'STATE_UNSPECIFIED'),
        )
        for field, value in invalid_fields:
            with self.subTest(field=field, value=value):
                item = protojson_budget()
                item['spendCap'][field] = value
                remote = Remote([item])
                with self.assertRaises(cap.CapError):
                    remote.invoke(apply=True)
                self.assertFalse(remote.mutations)

    def test_absent_output_state_cannot_certify_active_cap(self):
        for item in (budget(), protojson_budget(), budget(reconciling=True)):
            del item['spendCap']['outputState']
            with self.subTest(cap_fields=item['spendCap']):
                remote = Remote([item])
                with self.assertRaises(cap.CapError):
                    remote.invoke(apply=True)
                self.assertFalse(remote.mutations)

    def test_omitted_input_does_not_default_unspecified_output_to_configured(self):
        item = budget(state='STATE_UNSPECIFIED', reconciling=True)
        del item['spendCap']['inputState']
        remote = Remote([item])
        with self.assertRaises(cap.CapError):
            remote.invoke(apply=True)
        self.assertFalse(remote.mutations)

    def test_mismatched_or_lifted_existing_cap_is_not_changed(self):
        altered = []
        item = budget(); item['amount']['specifiedAmount']['units'] = '9'; altered.append(item)
        item = budget(); item['budgetFilter']['projects'] = ['projects/999999999999']; altered.append(item)
        item = budget(); item['budgetFilter']['services'] = ['services/OTHER']; altered.append(item)
        item = budget(); item['budgetFilter']['creditTypesTreatment'] = 'INCLUDE_ALL_CREDITS'; altered.append(item)
        item = budget(); item['thresholdRules'] = []; altered.append(item)
        item = budget(); item['notificationsRule']['disableDefaultIamRecipients'] = True; altered.append(item)
        item = budget(); del item['spendCap']; altered.append(item)
        altered.append(budget('AWAITING_NEXT_PERIOD'))
        item = budget(); item['spendCap']['inputState'] = 'AWAITING_NEXT_PERIOD'; altered.append(item)
        for index, item in enumerate(altered):
            with self.subTest(case=index):
                remote = Remote([item])
                with self.assertRaises(cap.CapError):
                    remote.invoke(apply=True)
                self.assertFalse(remote.mutations)

    def test_duplicate_dedicated_names_stop_without_mutation(self):
        remote = Remote([budget(), budget()])
        with self.assertRaises(cap.CapError):
            remote.invoke(apply=True)
        self.assertFalse(remote.mutations)

    def test_account_currency_active_status_and_project_link_are_required(self):
        for field, value in (
                ('currencyCode', None), ('currencyCode', 'USD'), ('open', False),
                ('name', 'billingAccounts/OTHER')):
            with self.subTest(account_field=field):
                remote = Remote()
                if value is None:
                    remote.account.pop(field)
                else:
                    remote.account[field] = value
                with self.assertRaises(cap.CapError):
                    remote.invoke(apply=True)
                self.assertFalse(remote.mutations)
        for field, value in (
                ('billingAccountName', 'billingAccounts/OTHER'), ('billingEnabled', False),
                ('projectId', 'another-project')):
            with self.subTest(link_field=field):
                remote = Remote()
                remote.link[field] = value
                with self.assertRaises(cap.CapError):
                    remote.invoke(apply=True)
                self.assertFalse(remote.mutations)

    def test_pagination_finds_existing_cap_before_considering_creation(self):
        remote = Remote([budget()])
        remote.pages = [{'budgets': [], 'nextPageToken': 'second'}, {'budgets': [budget()]}]
        self.assertEqual(remote.invoke(apply=True)['status'], 'configured')
        self.assertEqual(remote.page_count, 2)
        pages = [request for request in remote.requests if request.url.path.endswith('/budgets')]
        self.assertEqual(pages[1].url.params['pageToken'], 'second')
        self.assertFalse(remote.mutations)

    def test_unbounded_pagination_is_rejected_without_creation(self):
        remote = Remote()
        remote.endless_pages = True
        with self.assertRaises(cap.CapError):
            remote.invoke(apply=True)
        self.assertLessEqual(remote.page_count, 20)
        self.assertFalse(remote.mutations)

    def test_ambiguous_creation_is_not_retried_and_next_run_converges(self):
        for failure in ('timeout', 'http'):
            with self.subTest(failure=failure):
                remote = Remote()
                remote.create_failure = failure
                result = remote.invoke(apply=True)
                self.assertEqual(result['status'], 'creation_uncertain')
                self.assertEqual(len(remote.mutations), 1)
                self.assertNotIn(TOKEN, json.dumps(result))
                remote.create_failure = None
                self.assertEqual(remote.invoke(apply=True)['status'], 'configured')
                self.assertEqual(len(remote.mutations), 1)


class CommandLineTests(unittest.TestCase):
    def invoke(self, extra=(), failure=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-config.json'
            path.write_text(json.dumps(vars(configuration()), default=str), encoding='utf-8')
            output = io.StringIO()
            with patch.dict(os.environ, {'RESEARCH_OPERATOR_GOOGLE_ACCESS_TOKEN': TOKEN}), \
                    patch.object(cap.httpx, 'Client') as client, \
                    patch.object(cap, 'run', return_value={'status': 'planned'}, side_effect=failure) as run, \
                    redirect_stdout(output):
                result = cap.main(['--config', str(path), *extra])
            client.assert_called_once_with(trust_env=False, follow_redirects=False, timeout=10)
            return result, json.loads(output.getvalue()), run

    def test_apply_requires_explicit_cli_flag(self):
        for flags, expected in (((), False), (('--apply',), True)):
            with self.subTest(flags=flags):
                code, report, run = self.invoke(flags)
                self.assertEqual(code, 0)
                self.assertEqual(report['status'], 'planned')
                self.assertIs(run.call_args.kwargs['apply'], expected)
                self.assertEqual(run.call_args.args[2], TOKEN)

    def test_http_failure_logs_do_not_disclose_private_details(self):
        private_url = 'https://example.invalid/private-account/' + TOKEN
        error = httpx.ReadTimeout(private_url, request=httpx.Request('GET', private_url))
        code, report, _ = self.invoke(failure=error)
        self.assertEqual(code, 1)
        self.assertEqual(report, {'status': 'failed', 'category': 'ReadTimeout'})
        self.assertNotIn(TOKEN, json.dumps(report))
        self.assertNotIn('private-account', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
