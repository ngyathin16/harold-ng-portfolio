"""Managed install and restore permissions exercised through real login sessions."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from deploy import managed, migrate
from ops import backup, serverless
from ops.test_backup import Store
from service.budget import Budget, BudgetUnavailable, METRICS, install_window


BUDGET_TABLES = ('budget_windows', 'budget_metrics', 'budget_storage', 'budget_search_receipts',
                 'budget_migration_receipts', 'budget_object_receipts')


def role_options(url, role, passwords):
    values = conninfo_to_dict(url)
    values['user'] = 'research_' + role + '_login'
    values['password'] = passwords[role]
    return values


def login_budget(url, role, window, passwords):
    return Budget(None, window, connection=lambda: psycopg.connect(**role_options(url, role, passwords), row_factory=dict_row))


@pytest.fixture(scope='module')
def managed_database():
    url = os.environ.get('RESEARCH_MANAGED_INSTALL_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set an isolated managed *_test database owned by a non-administrative migration login')
    passwords = {key: 'managed-local-fixture-' + uuid.uuid4().hex for key in managed.LOGIN_GROUPS}
    with psycopg.connect(url, row_factory=dict_row) as connection:
        identity = connection.execute('''SELECT current_database() AS database,session_user AS role,
            rolsuper,rolbypassrls FROM pg_roles WHERE rolname=session_user''').fetchone()
        assert identity['database'].endswith('_test'), 'Refusing a non-test database'
        assert not identity['rolsuper'] and not identity['rolbypassrls'], 'Use a managed-style migration owner'
        baseline = managed.provider_baseline(connection)
        existing = connection.execute("SELECT to_regclass('public.research_schema_migrations') AS name").fetchone()['name']
        report = managed.apply(connection, passwords, baseline)
        if not existing:
            assert report['applied'] == list(migrate.MIGRATIONS)
        else:
            assert report['already_applied'] + len(report['applied']) == len(migrate.MIGRATIONS)
        assert managed.apply(connection, passwords, baseline)['applied'] == []
        window = 'managed-permission-' + uuid.uuid4().hex
        current = datetime.now(timezone.utc)
        install_window(connection, window, current-timedelta(minutes=1), current+timedelta(days=1),
                       dict.fromkeys(METRICS, 10**9), {}, 10**9, 100, 'Local managed permission fixture')
        publication = 'managed-private-' + uuid.uuid4().hex
        connection.execute("INSERT INTO research.datasets VALUES(%s,'Private fixture','','fixture','[]',NULL)", (publication,))
        connection.execute('''INSERT INTO research.publications(id,dataset_id,snapshot_id,manifest,scope,
            source_sha256,bundle_sha256,schema_fingerprint) VALUES(%s,%s,'snapshot','{}','{}','source','bundle','schema')''',
                           (publication, publication))
    try:
        yield dict(url=url, passwords=passwords, baseline=baseline, window=window, publication=publication)
    finally:
        with psycopg.connect(url) as connection:
            for table in ('budget_object_receipts', 'budget_migration_receipts', 'budget_search_receipts', 'budget_metrics'):
                connection.execute(sql.SQL('DELETE FROM research.{} WHERE window_id=%s').format(sql.Identifier(table)), (window,))
            connection.execute('DELETE FROM research.budget_windows WHERE id=%s', (window,))
            connection.execute('DELETE FROM research.budget_storage')
            connection.execute('DELETE FROM research.publications WHERE id=%s', (publication,))
            connection.execute('DELETE FROM research.datasets WHERE id=%s', (publication,))


def assert_ledger_writes_denied(url, role, passwords):
    with psycopg.connect(**role_options(url, role, passwords), row_factory=dict_row) as connection:
        assert connection.execute('SELECT session_user AS role').fetchone()['role'] == 'research_' + role + '_login'
        for table in BUDGET_TABLES:
            for action in ('DELETE FROM {}', 'TRUNCATE {}', 'INSERT INTO {} DEFAULT VALUES'):
                with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                    connection.execute(sql.SQL(action).format(sql.Identifier('research', table)))
        for table, assignment in (('budget_windows', 'armed=false'), ('budget_metrics', 'used=0'),
                                  ('budget_storage', 'ceiling=0'), ('budget_search_receipts', 'released=true'),
                                  ('budget_migration_receipts', 'released=true'), ('budget_object_receipts', "state='deleted'")):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(sql.SQL('UPDATE research.{} SET ' + assignment).format(sql.Identifier(table)))


def assert_worker_launch_permissions(url, passwords):
    with psycopg.connect(**role_options(url, 'worker', passwords)) as connection:
        connection.execute("UPDATE research.execution_reservations SET state='running',claimed_at=now() WHERE false")
        for assignment in ("launch_spec='{}'", "launch_protocol='legacy'", 'launch_confirmed_at=now()', "operation_name='forged'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute('UPDATE research.execution_reservations SET ' + assignment + ' WHERE false')


def test_managed_importer_can_import_but_cannot_edit_any_ledger_table(managed_database):
    values = managed_database
    assert_ledger_writes_denied(values['url'], 'importer', values['passwords'])
    with psycopg.connect(**role_options(values['url'], 'importer', values['passwords']), row_factory=dict_row) as connection:
        connection.execute("UPDATE research.datasets SET description='Imported through restricted login' WHERE id=%s", (values['publication'],))
        assert connection.execute('SELECT description FROM research.datasets WHERE id=%s', (values['publication'],)).fetchone()['description'].startswith('Imported')
        for table in BUDGET_TABLES:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(sql.SQL('SELECT * FROM research.{}').format(sql.Identifier(table)))
    login_budget(values['url'], 'importer', values['window'], values['passwords']).invocation(operator=True)


def test_managed_backup_reads_private_rows_and_only_executes_authorized_budget_functions(managed_database):
    values = managed_database
    assert_ledger_writes_denied(values['url'], 'backup', values['passwords'])
    with psycopg.connect(**role_options(values['url'], 'backup', values['passwords']), row_factory=dict_row) as connection:
        assert connection.execute('SELECT id FROM research.publications WHERE id=%s', (values['publication'],)).fetchone()['id'] == values['publication']
        assert connection.execute('SELECT id FROM research.budget_windows WHERE id=%s', (values['window'],)).fetchone()['id'] == values['window']
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute("UPDATE research.publications SET source_sha256='forged'")
        for function, arguments in (
            ('budget_reserve_search', (values['window'], 'missing', 1, 1)),
            ('budget_authorize_launch', (values['window'], 'missing', 1, 1)),
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(sql.SQL('SELECT research.{}(%s,%s,%s,%s)').format(sql.Identifier(function)), arguments)
    budget = login_budget(values['url'], 'backup', values['window'], values['passwords'])
    budget.invocation(operator=True)
    budget.result_charge('get')
    identity = hashlib.sha256(b'local-permission-empty-object').hexdigest()
    assert budget.prepare_object(identity, 0, hashlib.sha256(b'').hexdigest()) == 'new'
    assert budget.get_object_receipt(identity)['state'] == 'planned'
    budget.verify_object(identity)
    assert budget.get_object_receipt(identity)['state'] == 'verified'
    assert budget.delete_object(identity) == 0
    assert budget.get_object_receipt(identity)['state'] == 'deleted'


@pytest.mark.parametrize('role', ['api', 'worker', 'dispatcher'])
def test_runtime_logins_cannot_create_operator_storage_receipts(managed_database, role):
    values = managed_database
    assert_ledger_writes_denied(values['url'], role, values['passwords'])
    budget = login_budget(values['url'], role, values['window'], values['passwords'])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        budget.prepare_object('a'*64, 1, 'b'*64)
    if role == 'worker':
        assert_worker_launch_permissions(values['url'], values['passwords'])


def test_actual_backup_restore_preserves_permissions_and_disarms_copied_allowance(managed_database, tmp_path):
    values = managed_database
    target = os.environ.get('RESEARCH_MANAGED_RESTORE_TEST_DATABASE_URL')
    if not target:
        pytest.skip('Set a distinct empty *_restore_test database for the actual dump/restore test')
    with psycopg.connect(target, row_factory=dict_row) as connection:
        assert connection.execute('SELECT current_database() AS name').fetchone()['name'].endswith('_restore_test')
        baseline = managed.provider_baseline(connection)
    archive = Store()
    source = psycopg.conninfo.make_conninfo(**role_options(values['url'], 'backup', values['passwords']))
    native_pg_command = backup.pg_command
    def permission_dump(tool, url, arguments, **options):
        # This test checks real dump/restore permissions; Linux resource limits
        # remain mandatory in the production operator and its separate tests.
        maximum = options.pop('max_file_bytes', None)
        result = native_pg_command(tool, url, arguments,
            max_file_bytes=maximum if os.name == 'posix' else None, **options)
        if maximum is not None:
            output = next(value[7:] for value in arguments if value.startswith('--file='))
            assert Path(output).stat().st_size <= maximum
        return result
    with patch.dict(os.environ, {'RESEARCH_APP_COMMIT': 'a'*40}), patch.object(backup, 'pg_command', side_effect=permission_dump):
        manifest = backup.prepare_set(source, None, tmp_path/'prepared', 'managed-budget', managed=True,
            max_staging_bytes=512*1024*1024, max_dump_bytes=64*1024*1024)
    backup.publish_set(archive, tmp_path/'prepared', manifest)
    with patch.dict(os.environ, {'RESEARCH_RESTORE_BASELINE_JSON': json.dumps(baseline)}):
        report = serverless.restore(archive, Store(), 'managed-budget', target, tmp_path/'restored', 512*1024*1024)
    assert report['passed'] and report['dispatch_paused']
    with psycopg.connect(target, row_factory=dict_row) as connection:
        restored = connection.execute('SELECT armed,authority_database FROM research.budget_windows WHERE id=%s', (values['window'],)).fetchone()
        assert restored['armed'] is False
        assert restored['authority_database'] == conninfo_to_dict(values['url'])['dbname']
        assert managed.apply(connection, values['passwords'], baseline)['applied'] == []
        assert managed.audit(connection)['permission_violations'] == []
    assert_ledger_writes_denied(target, 'importer', values['passwords'])
    assert_ledger_writes_denied(target, 'backup', values['passwords'])
    assert_worker_launch_permissions(target, values['passwords'])
    with pytest.raises(BudgetUnavailable):
        login_budget(target, 'backup', values['window'], values['passwords']).result_charge('get')
    login_budget(values['url'], 'backup', values['window'], values['passwords']).result_charge('get')
