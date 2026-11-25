"""Qualify a clean free-project import using restricted logins and local artifacts.

Credentials come only from RESEARCH_IMPORT_DATABASE_URL,
RESEARCH_API_DATABASE_URL, and RESEARCH_BACKUP_DATABASE_URL. This writes no
searches and does not qualify R2 storage or backup recovery.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import quote

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.import_snapshot import LocalStorage, packed, publish, verify_bundle
from service.app import create_app
from service.config import Settings


def read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8-sig'))


def private_directory(path):
    path = path.resolve()
    if path.is_relative_to(ROOT.parents[1]):
        raise ValueError('Artifacts and scratch must remain outside the repository')
    path.mkdir(parents=True, exist_ok=True)
    return path


def connection_url(name, endpoint, role):
    url = os.environ[name]
    options = conninfo_to_dict(url)
    if options.get('sslmode') != 'verify-full' or options.get('hostaddr'):
        raise ValueError('Qualification requires verified hostname TLS')
    if not options.get('host', '').startswith(endpoint + '.') or '-pooler.' in options['host']:
        raise ValueError('Connection must use the exact dedicated direct endpoint')
    if options.get('user') != role:
        raise ValueError('Qualification requires the specified restricted login')
    return url


def private_artifacts(connection, publication):
    return connection.execute('''SELECT publication_id,id,storage_key,size,sha256
        FROM research.artifacts WHERE publication_id=%s ORDER BY id''', (publication,)).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'storage', 'scratch', 'output', 'provider_before', 'branch_before', 'plan_evidence'):
        parser.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--endpoint-id', required=True)
    parser.add_argument('--expected-bundle-sha256', required=True)
    args = parser.parse_args()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'project_id': args.project_id,
              'import_passed': False, 'runtime_reads_passed': False, 'backup_private_visibility_passed': False,
              'qualified_for_production': False, 'artifact_storage': 'private local experiment directory',
              'r2_recovery_drill': False, 'searches_executed': False, 'historical_results_imported': False,
              'stages': []}

    def stage(name):
        report['stages'].append({'name': name, 'at': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'stage': name}), flush=True)

    started = time.monotonic()
    try:
        stage('preflight')
        project = read_json(args.provider_before)['project']
        branch = read_json(args.branch_before)['branch']
        organizations = read_json(args.plan_evidence)
        organization = next(item for item in organizations if item['id'] == project['org_id'])
        if organization['plan'] != 'free' or project['id'] != args.project_id:
            raise ValueError('Dedicated project must be verified on the Free plan')
        limit = project['branch_logical_size_limit_bytes']
        logical_size = branch['logical_size']
        if limit - logical_size < 512 * 1024**2:
            raise ValueError('Free branch has insufficient import headroom')
        report['plan'] = 'free'
        report['provider_before'] = {'branch_logical_size_bytes': logical_size,
            'branch_logical_size_limit_bytes': limit, 'project_synthetic_storage_size_bytes': project.get('synthetic_storage_size'),
            'note': 'Provider API snapshot; logical and synthetic values are distinct from pg_database_size.'}
        bundle = verify_bundle(args.bundle)
        if bundle['bundle_sha256'] != args.expected_bundle_sha256:
            raise ValueError('Source bundle differs from the retained verified publication')
        publication = bundle['publication_id']
        report.update(publication_id=publication, source_sha256=bundle['source_sha256'],
            bundle_sha256=bundle['bundle_sha256'], source_tables=sum(d['kind'] == 'table' for d in bundle['tables'].values()),
            source_rows=sum(d['row_count'] for d in bundle['tables'].values() if d['kind'] == 'table'),
            views=sum(d['kind'] == 'view' for d in bundle['tables'].values()),
            artifacts=len(bundle['artifacts']), artifact_bytes=sum(a['size'] for a in bundle['artifacts']),
            max_restored_bytes=256 * 1024**2, max_verification_bytes=256 * 1024**2)
        storage = LocalStorage(private_directory(args.storage))
        tempfile.tempdir = str(private_directory(args.scratch))
        importer_url = connection_url('RESEARCH_IMPORT_DATABASE_URL', args.endpoint_id, 'research_importer_login')
        api_url = connection_url('RESEARCH_API_DATABASE_URL', args.endpoint_id, 'research_api_login')
        backup_url = connection_url('RESEARCH_BACKUP_DATABASE_URL', args.endpoint_id, 'research_backup_login')
        with psycopg.connect(importer_url, row_factory=dict_row) as conn:
            if not conn.pgconn.ssl_in_use:
                raise ValueError('Import connection is not encrypted')
            role = conn.execute('SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
            if role['rolsuper'] or role['rolbypassrls']:
                raise ValueError('Importer must not bypass row policies')
            before = conn.execute('''SELECT
                (SELECT count(*) FROM research.publications) AS publications,
                (SELECT count(*) FROM research.query_runs) AS searches,
                (SELECT paused FROM research.dispatch_control WHERE singleton) AS paused,
                pg_database_size(current_database()) AS database_allocated_bytes''').fetchone()
            if before['publications'] or before['searches'] or not before['paused']:
                raise ValueError('Clean qualification requires no publications or searches and paused dispatch')
            report['before'] = before
            report['importer_role'] = role
        stage('restricted_managed_import')
        import_started = time.monotonic()
        report['import'] = publish(args.bundle, importer_url, storage, managed=True,
            max_restored_bytes=report['max_restored_bytes'], max_verification_bytes=report['max_verification_bytes'])
        report['import_seconds'] = round(time.monotonic() - import_started, 3)
        report['import_passed'] = report['import']['status'] == 'published'
        if not report['import_passed']:
            raise ValueError('Expected a clean first publication import')
        stage('runtime_api_reads')
        settings = Settings(database_url=api_url, deployment_mode='serverless', client_ip_mode='shared',
                            session_secret='isolated-import-read-verification')
        responses = {}
        base = '/v1/publications/' + publication
        with ConnectionPool(api_url, min_size=0, max_size=2, kwargs={'row_factory': dict_row}) as pool, \
                TestClient(create_app(settings, pool=pool, markers=object())) as client:
            def get(path):
                response = client.get(path)
                responses[path] = response.status_code
                if response.status_code != 200:
                    raise ValueError('Published API read failed: ' + path)
                return response.json()
            datasets = get('/v1/datasets')['datasets']
            if bundle['dataset']['id'] not in {item['id'] for item in datasets}:
                raise ValueError('Published dataset is not visible')
            get('/v1/datasets/' + bundle['dataset']['id'] + '/publications')
            if get(base)['bundle_sha256'] != bundle['bundle_sha256']:
                raise ValueError('API publication identity differs')
            tables = get(base + '/tables')['tables']
            actual = {item['name']: (item['row_count'], item['logical_sha256']) for item in tables}
            expected = {name: (item['row_count'], item['logical_sha256']) for name, item in bundle['tables'].items()}
            if actual != expected:
                raise ValueError('Imported table/view verification differs from bundle')
            catalog = get(base + '/catalog?page_size=2')
            if catalog['total'] != bundle['tables']['research_catalog']['row_count']:
                raise ValueError('Published catalog count differs')
            if catalog['rows']:
                get(base + '/records/' + quote(catalog['rows'][0]['id'], safe=''))
            first = get(base + '/tables/entities?page_size=2')
            second = get(base + '/tables/entities?page_size=2&page=2')
            if {row['entity_id'] for row in first['rows']} & {row['entity_id'] for row in second['rows']}:
                raise ValueError('Published table pagination overlaps')
            get(base + '/coverage')
            get(base + '/rules')
            retired = client.get(base + '/downloads')
            responses[base + '/downloads'] = retired.status_code
            if retired.status_code != 404:
                raise ValueError('Retired download route must remain unavailable')
        report['api_responses'] = responses
        report['table_view_parity'] = {name: {'rows': value[0], 'logical_sha256': value[1]} for name, value in actual.items()}
        report['runtime_reads_passed'] = True
        stage('backup_private_visibility')
        with psycopg.connect(importer_url, row_factory=dict_row) as importer, psycopg.connect(backup_url, row_factory=dict_row) as backup:
            importer_rows = private_artifacts(importer, publication)
            backup_rows = private_artifacts(backup, publication)
            if importer_rows != backup_rows or len(backup_rows) != len(bundle['artifacts']):
                raise ValueError('Restricted backup cannot see all private artifact rows')
            backup_role = backup.execute('''SELECT rolname,rolsuper,rolbypassrls,
                pg_has_role(current_user,'neon_superuser','MEMBER') AS provider_admin,
                (SELECT pg_has_role(current_user,datdba,'MEMBER') FROM pg_database WHERE datname=current_database()) AS database_owner
                FROM pg_roles WHERE rolname=current_user''').fetchone()
            if any(value for key, value in backup_role.items() if key != 'rolname'):
                raise ValueError('Backup reader has administrative privileges')
            rls = backup.execute("SELECT relrowsecurity FROM pg_class WHERE oid='research.artifacts'::regclass").fetchone()['relrowsecurity']
            if not rls:
                raise ValueError('Private artifact row security is disabled')
            report['backup_private_rows'] = {'table': 'research.artifacts', 'rows': len(backup_rows),
                'rls_enabled': rls, 'exact_rows_equal': True,
                'ordered_metadata_sha256': hashlib.sha256(packed(backup_rows).encode()).hexdigest(), 'role': backup_role}
            report['after'] = importer.execute('''SELECT
                (SELECT count(*) FROM research.publications) AS publications,
                (SELECT count(*) FROM research.query_runs) AS searches,
                (SELECT count(*) FROM research.query_results) AS query_results,
                (SELECT paused FROM research.dispatch_control WHERE singleton) AS paused,
                pg_database_size(current_database()) AS database_allocated_bytes''').fetchone()
        with psycopg.connect(api_url) as api:
            try:
                api.execute('SELECT count(*) FROM research.artifacts')
            except psycopg.errors.InsufficientPrivilege:
                api.rollback()
                report['backup_private_rows']['api_artifact_read_denied'] = True
            else:
                raise ValueError('API must not read private artifact metadata')
        if not report['after']['paused'] or report['after']['searches'] or report['after']['query_results']:
            raise ValueError('Qualification must preserve paused search-free state')
        report['backup_private_visibility_passed'] = True
        report['provider_billed_storage_bytes'] = None
        report['limitations'] = ['Local artifacts are not deployed R2 source objects.',
            'Private-row visibility is not a completed backup or restore drill.',
            'API checks execute locally against hosted PostgreSQL; Cloud Run latency is unmeasured.',
            'API GET requests write ordinary read-rate-limit counters.',
            'Search result storage, concurrency, and all-provider budget qualification remain pending.']
        stage('complete')
    except Exception as error:
        report['failure'] = {'stage': report['stages'][-1]['name'] if report['stages'] else 'setup',
            'type': type(error).__name__, 'sqlstate': getattr(error, 'sqlstate', None)}
        if isinstance(error, ValueError):
            report['failure']['detail'] = str(error)
        private_error = os.environ.get('RESEARCH_QUALIFICATION_ERROR_PATH')
        if private_error:
            message = str(error)
            for name in ('RESEARCH_IMPORT_DATABASE_URL', 'RESEARCH_API_DATABASE_URL', 'RESEARCH_BACKUP_DATABASE_URL'):
                value = os.environ.get(name)
                if value:
                    password = conninfo_to_dict(value).get('password')
                    if password:
                        message = message.replace(password, '[REDACTED]')
            Path(private_error).write_text(message, encoding='utf-8')
    report['seconds'] = round(time.monotonic() - started, 3)
    args.output.write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({name: report.get(name) for name in ('import_passed', 'runtime_reads_passed', 'backup_private_visibility_passed', 'failure')}))
    return 1 if 'failure' in report else 0


if __name__ == '__main__':
    raise SystemExit(main())
