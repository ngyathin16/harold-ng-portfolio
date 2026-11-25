"""Every paid object call and operator entry crosses the committed allowance."""
from contextlib import nullcontext
import hashlib
import io
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError, ReadTimeoutError
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ops import backup, serverless
from ops.test_backup_copy import ENDPOINT, ETAG, RawBody
from service.budget import Budget, BudgetUnavailable
from service.test_budget import ledger, usage


@pytest.fixture
def charged_store():
    charges = []
    def charge(kind, **values):
        charges.append((kind, values))
    env = dict(R2_ENDPOINT=ENDPOINT, R2_BUCKET='private', R2_ACCESS_KEY_ID='fixture-access',
               R2_SECRET_ACCESS_KEY='fixture-secret')
    env.update({name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE') if name in os.environ})
    with patch.dict(os.environ, env, clear=True):
        store = backup.R2Store(namespace='backups', charge=charge)
    try:
        yield store, charges
    finally:
        store.client.close()


def test_all_object_calls_are_charged_before_sdk_invocation(charged_store, tmp_path):
    store, charges = charged_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    cases = [
        ('get_object', 'get', 0, lambda: store.read('item'), {'Body': io.BytesIO()}),
        ('get_object', 'get', 0, lambda: store.read_for_copy('item'), {'Body': io.BytesIO(), 'ETag': ETAG}),
        ('copy_object', 'copy', 0, lambda: store.copy('item', backup.CopySource(ENDPOINT, 'source', 'item', ETAG)), {}),
        ('put_object', 'put', 7, lambda: store.put('item', path), {}),
        ('delete_object', 'delete', 0, lambda: store.delete('item'), {}),
    ]
    for method, kind, size, invoke, response in cases:
        before = len(charges)
        def called(**kwargs):
            assert len(charges) == before + 1
            assert charges[-1] == (kind, {'bytes': size})
            if method == 'put_object':
                assert kwargs['ContentLength'] == 7
                assert kwargs['Body'].read() == b'payload'
            return response
        with patch.object(store.client, method, side_effect=called) as operation:
            invoke()
            assert operation.call_count == 1


def test_each_listing_page_is_charged_before_fetch_and_exhaustion_stops_it(charged_store):
    store, charges = charged_store
    def pages():
        assert charges == [('list', {'bytes': 0})]
        yield {'IsTruncated': True, 'Contents': [{'Key': 'backups/one'}]}
        assert charges == [('list', {'bytes': 0}), ('list', {'bytes': 0})]
        yield {'IsTruncated': False, 'Contents': [{'Key': 'backups/two'}]}
    with patch.object(store.client, 'get_paginator', return_value=Mock(paginate=lambda **kwargs: pages())):
        assert list(store.keys()) == ['one', 'two']
    assert len(charges) == 2
    def unavailable(kind, **kwargs):
        raise BudgetUnavailable('exhausted')
    store.charge = unavailable
    with patch.object(store.client, 'get_paginator', return_value=Mock(paginate=Mock(return_value=iter([])))) as paginator:
        with pytest.raises(BudgetUnavailable):
            list(store.keys())
        # The paginator is created locally; its iterator is never advanced.
        assert paginator.call_count == 1


def test_exhausted_allowance_never_makes_a_put(charged_store, tmp_path):
    store, charges = charged_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    store.charge = Mock(side_effect=BudgetUnavailable('exhausted'))
    with patch.object(store.client, 'put_object') as put, pytest.raises(BudgetUnavailable):
        store.put('item', path)
    put.assert_not_called()


def test_metered_legacy_client_never_retries_lost_acknowledgement(charged_store):
    store, charges = charged_store
    assert store.client.meta.config.retries['total_max_attempts'] == 1
    with patch.object(store.client._endpoint.http_session, 'send', side_effect=ReadTimeoutError(endpoint_url=ENDPOINT)) as send:
        with pytest.raises(ReadTimeoutError):
            store.copy('item', backup.CopySource(ENDPOINT, 'source', 'item', ETAG))
    assert send.call_count == 1
    assert charges == [('copy', {'bytes': 0})]


def test_metered_client_blocks_redirect_before_second_request(charged_store):
    store, charges = charged_store
    body = b'<Error><Code>PermanentRedirect</Code><Endpoint>other.invalid</Endpoint></Error>'
    with patch.object(store.client._endpoint.http_session, 'send',
        return_value=AWSResponse(ENDPOINT, 301, {'content-type': 'application/xml'}, RawBody(body))) as send:
        with pytest.raises(ValueError, match='redirect'):
            store.read('item')
    assert send.call_count == 1
    assert charges == [('get', {'bytes': 0})]


def test_result_adapter_preserves_the_same_accounting_callback(charged_store):
    store, charges = charged_store
    objects = SimpleNamespace(client=store.client, bucket='private', endpoint=ENDPOINT, prefix='results', charge=store.charge)
    budget = SimpleNamespace(result_charge=store.charge)
    with pytest.raises(ValueError, match='storage accounting'):
        backup.R2Store.from_result_objects(objects, budget=None)
    adapted = backup.R2Store.from_result_objects(objects, budget=budget)
    assert adapted.charge is store.charge
    assert adapted.budget is budget
    with patch.object(store.client, 'get_object', return_value={'Body': io.BytesIO()}):
        adapted.read('item')
    assert charges == [('get', {'bytes': 0})]


@pytest.mark.parametrize('command,expected', [('backup', 'source'), ('restore', 'source')])
def test_operator_budget_uses_an_independent_source_pool_before_work(command, expected):
    source = 'postgresql://reader@source.invalid/source?sslmode=verify-full'
    target = 'postgresql://owner@target.invalid/isolated_restore_test?sslmode=verify-full'
    env = {'RESEARCH_BUDGET_WINDOW_ID': 'reviewed', 'RESEARCH_RESULT_STORAGE_MODE': 'objects',
           'RESEARCH_OPERATOR_DATABASE_URL': target if command == 'restore' else source,
           'RESEARCH_BACKUP_DATABASE_URL': source, 'RESEARCH_OPERATOR_ENDPOINT_MODE': 'direct'}
    pool = Mock()
    with patch.dict(os.environ, env, clear=True), patch.object(serverless, 'ConnectionPool', return_value=nullcontext(pool)) as factory, \
            patch('service.budget.Budget') as budget:
        with serverless.operator_budget(command) as result:
            assert result is budget.return_value
            budget.return_value.invocation.assert_called_once_with(operator=True)
        assert factory.call_args.args == (source,)
        assert factory.call_args.kwargs['max_size'] == 1
        budget.assert_called_once_with(pool, 'reviewed')


def test_restore_database_override_changes_only_database_and_precedes_tls_and_restore(tmp_path):
    source = 'postgresql://reader:source-fixture@source.invalid/live?sslmode=verify-full'
    target = make_conninfo(host='target.invalid', port=5432, user='owner', password='fixture p@ss',
                          dbname='old_restore_test', sslmode='verify-full', application_name='restore-fixture',
                          options='-c statement_timeout=1000')
    env = {'RESEARCH_OPERATOR_DATABASE_URL': target, 'RESEARCH_BACKUP_DATABASE_URL': source,
           'RESEARCH_RESTORE_DATABASE_NAME': 'new_isolated_restore_test', 'RESEARCH_OPERATOR_ENDPOINT_MODE': 'direct',
           'RESEARCH_OPERATOR_MAX_STAGING_BYTES': '1000', 'RESEARCH_OBJECT_PREFIX': 'objects',
           'RESEARCH_RESTORE_OBJECT_PREFIX': 'restored-objects'}
    urls = []
    def check_tls(url, **kwargs):
        urls.append(url)
        if len(urls) == 1:
            assert conninfo_to_dict(url) == {**conninfo_to_dict(target), 'dbname': 'new_isolated_restore_test'}
    def store(*args, namespace=None, **kwargs):
        return SimpleNamespace(bucket='private', prefix=(args[1] if len(args) > 1 else namespace or 'backups'))
    expected_url = make_conninfo(target, dbname='new_isolated_restore_test')
    with patch.dict(os.environ, env, clear=True), patch.object(serverless, 'require_tls', side_effect=check_tls), \
            patch.object(backup, 'R2Store', side_effect=store), \
            patch.object(backup, 'database_operation_lock', return_value=nullcontext(Mock())) as lock, \
            patch.object(serverless, 'restore', return_value={'passed': True}) as restore:
        assert serverless.operate('restore', tmp_path, 'fixture')['passed']
        lock.assert_called_once_with(source)
        assert restore.call_args.args[3] == expected_url
    assert urls == [expected_url, source]


@pytest.mark.parametrize('value', ['', 'live', 'Upper_restore_test', 'a-b_restore_test',
                                   'x\n_restore_test', 'a'*51 + '_restore_test', 'x_restore_test host=other'])
def test_invalid_restore_database_override_is_rejected_before_connection_or_object_work(value, tmp_path):
    with patch.dict(os.environ, {'RESEARCH_OPERATOR_DATABASE_URL': 'postgresql://owner@target/old_restore_test',
                                'RESEARCH_RESTORE_DATABASE_NAME': value}, clear=True), \
            patch.object(serverless, 'require_tls') as tls, patch.object(backup, 'R2Store') as store:
        with pytest.raises(ValueError, match='Restore database name'):
            serverless.operate('restore', tmp_path)
        tls.assert_not_called()
        store.assert_not_called()


@pytest.mark.parametrize('command', ['backup', 'verify', 'prune', 'cleanup', 'import'])
def test_restore_database_override_is_rejected_for_other_commands(command):
    with patch.dict(os.environ, {'RESEARCH_OPERATOR_DATABASE_URL': 'postgresql://owner@target/live',
                                'RESEARCH_RESTORE_DATABASE_NAME': 'isolated_restore_test'}, clear=True):
        with pytest.raises(ValueError, match='only valid for the restore'):
            serverless.operator_database_url(command)


def test_restore_override_rejects_source_database_and_preserves_default_without_override():
    target = 'postgresql://owner:fixture@target.invalid/original_restore_test?sslmode=verify-full'
    env = {'RESEARCH_OPERATOR_DATABASE_URL': target,
           'RESEARCH_BACKUP_DATABASE_URL': 'postgresql://reader@source.invalid/source_restore_test'}
    with patch.dict(os.environ, env, clear=True):
        assert serverless.operator_database_url('restore') == target
        with patch.dict(os.environ, {'RESEARCH_RESTORE_DATABASE_NAME': 'source_restore_test'}):
            with pytest.raises(ValueError, match='differ from the live source'):
                serverless.operator_database_url('restore')


def test_cloud_objects_refuse_unmetered_operator_and_store():
    with patch.dict(os.environ, {'RESEARCH_RESULT_STORAGE_MODE': 'objects'}, clear=True), patch.object(serverless, 'ConnectionPool') as pool:
        with pytest.raises(BudgetUnavailable), serverless.operator_budget('backup'):
            pass
        with pytest.raises(ValueError, match='storage accounting'):
            backup.R2Store()
        with pytest.raises(ValueError, match='storage accounting'):
            backup.R2Store(charge=Mock())
        pool.assert_not_called()
    with patch.dict(os.environ, {'RESEARCH_RESULT_CREDENTIALS_JSON': '{}'}, clear=True):
        with pytest.raises(ValueError, match='storage accounting'):
            backup.R2Store(charge=Mock())


def test_failed_work_report_uses_the_same_budget_without_bypass(tmp_path, capsys):
    budget = Mock()
    stores = []
    def store_factory(*args, namespace=None, budget=None):
        assert budget is expected_budget
        store = Mock(bucket='private', prefix=namespace or 'backups')
        stores.append(store)
        return store
    expected_budget = budget
    with patch.dict(os.environ, {'RESEARCH_REPORT_PREFIX': 'reports'}, clear=True), \
            patch.object(serverless, 'operate', side_effect=RuntimeError('private failure')), \
            patch.object(backup, 'R2Store', side_effect=store_factory):
        assert serverless.run('backup', budget) == 1
    assert stores[0].put.call_count == 1
    assert 'private failure' not in capsys.readouterr().out


def test_backup_readonly_snapshot_cannot_rollback_committed_object_debit(ledger, charged_store):
    owner_pool, window = ledger
    store, _ = charged_store
    role = 'operator_budget_' + uuid.uuid4().hex[:16]
    password = 'local-fixture-' + uuid.uuid4().hex
    with owner_pool.connection() as connection:
        connection.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}').format(
            sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL('GRANT research_budget_operator TO {}').format(sql.Identifier(role)))
    options = conninfo_to_dict(owner_pool.conninfo)
    options.update(user=role, password=password)
    try:
        with ConnectionPool(make_conninfo(**options), min_size=0, max_size=1,
                            kwargs={'row_factory': dict_row}) as independent:
            store.charge = Budget(independent, window).result_charge
            with psycopg.connect(**options, row_factory=dict_row) as snapshot:
                snapshot.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                snapshot.execute('SELECT 1')
                def failed_network(**kwargs):
                    assert usage(owner_pool, window, 'r2_class_b') == 1
                    raise RuntimeError('lost response')
                with patch.object(store.client, 'get_object', side_effect=failed_network), pytest.raises(RuntimeError):
                    store.read('retained')
                snapshot.rollback()
            assert usage(owner_pool, window, 'r2_class_b') == 1
    finally:
        with owner_pool.connection() as connection:
            connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


class PhysicalBudget:
    def __init__(self):
        self.receipts, self.used, self.ceiling, self.calls = {}, 0, 1000, []

    def result_charge(self, kind, **values):
        self.calls.append((kind, values))

    def prepare_object(self, identity, size, sha256):
        existing = self.receipts.get(identity)
        if existing:
            if existing['state'] == 'deleted' or (existing['size'], existing['sha256']) != (size, sha256):
                raise BudgetUnavailable('receipt cannot be reused')
            return existing['state']
        if self.used + size > self.ceiling:
            raise BudgetUnavailable('storage exhausted')
        self.used += size
        self.receipts[identity] = dict(size=size, sha256=sha256, state='planned')
        return 'new'

    def get_object_receipt(self, identity):
        return self.receipts.get(identity)

    def verify_object(self, identity):
        self.receipts[identity]['state'] = 'verified'

    def delete_object(self, identity):
        receipt = self.receipts[identity]
        assert receipt['state'] == 'verified'
        self.used -= receipt['size']
        receipt['state'] = 'deleted'


def absent(operation):
    return ClientError({'Error': {'Code': 'NoSuchKey'}, 'ResponseMetadata': {'HTTPStatusCode': 404}}, operation)


@pytest.fixture
def physical_store(charged_store):
    store, _ = charged_store
    store.budget = PhysicalBudget()
    store.charge = store.budget.result_charge
    contents = {}
    def get(Bucket, Key):
        if (Bucket, Key) not in contents:
            raise absent('GetObject')
        return {'Body': io.BytesIO(contents[Bucket, Key]), 'ETag': ETAG}
    def head(Bucket, Key):
        if (Bucket, Key) not in contents:
            raise absent('HeadObject')
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}
    def put(Bucket, Key, Body, **kwargs):
        assert store.budget.get_object_receipt(store.receipt_identity(Key.removeprefix(store.prefix + '/')))['state'] == 'planned'
        contents[Bucket, Key] = Body.read()
        return {}
    def copy(Bucket, Key, CopySource, **kwargs):
        assert store.budget.get_object_receipt(store.receipt_identity(Key.removeprefix(store.prefix + '/')))['state'] == 'planned'
        contents[Bucket, Key] = contents[CopySource['Bucket'], CopySource['Key']]
        return {}
    def delete(Bucket, Key):
        contents.pop((Bucket, Key), None)
        return {'ResponseMetadata': {'HTTPStatusCode': 204}}
    with patch.multiple(store.client, get_object=Mock(side_effect=get), head_object=Mock(side_effect=head),
                        put_object=Mock(side_effect=put), copy_object=Mock(side_effect=copy),
                        delete_object=Mock(side_effect=delete)):
        yield store, contents


def test_physical_put_reserves_before_write_and_retries_only_verify(physical_store, tmp_path):
    store, contents = physical_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    store.put('member', path)
    store.put('member', path)
    assert store.client.put_object.call_count == 1
    assert store.client.get_object.call_count == 2
    assert store.budget.used == 7
    assert store.budget.get_object_receipt(store.receipt_identity('member'))['state'] == 'verified'
    assert contents['private', 'backups/member'] == b'payload'


@pytest.mark.parametrize('written', [True, False])
def test_lost_write_acknowledgement_never_allows_a_second_write(physical_store, tmp_path, written):
    store, contents = physical_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    def lost(**kwargs):
        if written:
            contents[kwargs['Bucket'], kwargs['Key']] = kwargs['Body'].read()
        raise ReadTimeoutError(endpoint_url=ENDPOINT)
    store.client.put_object.side_effect = lost
    with pytest.raises(ReadTimeoutError):
        store.put('member', path)
    if written:
        store.put('member', path)
        assert store.budget.get_object_receipt(store.receipt_identity('member'))['state'] == 'verified'
    else:
        with pytest.raises(backup.UncertainObjectWrite):
            store.put('member', path)
        with pytest.raises(backup.UncertainObjectWrite):
            store.delete('member')
        store.client.delete_object.assert_not_called()
        assert store.budget.get_object_receipt(store.receipt_identity('member'))['state'] == 'planned'
    assert store.client.put_object.call_count == 1
    assert store.budget.used == 7


def test_copy_requires_verified_metadata_and_reserves_before_single_copy(physical_store):
    store, contents = physical_store
    source = backup.CopySource(ENDPOINT, 'source', 'original', ETAG)
    with pytest.raises(ValueError, match='fully verified'):
        store.copy('member', source)
    assert store.budget.used == 0
    contents['source', 'original'] = b'payload'
    source = backup.CopySource(ENDPOINT, 'source', 'original', ETAG, 7, hashlib.sha256(b'payload').hexdigest())
    store.copy('member', source)
    store.copy('member', source)
    assert store.client.copy_object.call_count == 1
    assert store.client.get_object.call_count == 2
    assert store.budget.used == 7


def test_physical_storage_exhaustion_prevents_any_network_write(physical_store, tmp_path):
    store, _ = physical_store
    store.budget.ceiling = 3
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    with pytest.raises(BudgetUnavailable):
        store.put('member', path)
    store.client.put_object.assert_not_called()
    assert store.budget.used == 0


def test_verified_delete_refunds_only_after_head_absence_and_prevents_key_reuse(physical_store, tmp_path):
    store, contents = physical_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    store.put('member', path)
    store.delete('member')
    assert store.budget.used == 0
    assert store.budget.get_object_receipt(store.receipt_identity('member'))['state'] == 'deleted'
    assert ('private', 'backups/member') not in contents
    store.delete('member')
    assert store.client.delete_object.call_count == 1
    with pytest.raises(BudgetUnavailable):
        store.put('member', path)
    assert store.client.put_object.call_count == 1


def test_planned_present_object_is_verified_before_delete_and_refund(physical_store):
    store, contents = physical_store
    identity = store.receipt_identity('member')
    store.budget.prepare_object(identity, 7, hashlib.sha256(b'payload').hexdigest())
    contents['private', 'backups/member'] = b'payload'
    store.delete('member')
    assert store.client.get_object.call_count == 1
    assert store.client.delete_object.call_count == 1
    assert store.budget.used == 0
    assert store.budget.get_object_receipt(identity)['state'] == 'deleted'


def test_lost_delete_acknowledgement_retains_bytes_until_absence_is_proven(physical_store, tmp_path):
    store, contents = physical_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    store.put('member', path)
    original = store.client.delete_object.side_effect
    def lost(**kwargs):
        original(**kwargs)
        raise ReadTimeoutError(endpoint_url=ENDPOINT)
    store.client.delete_object.side_effect = lost
    with pytest.raises(ReadTimeoutError):
        store.delete('member')
    assert store.budget.used == 7
    store.client.delete_object.side_effect = original
    store.delete('member')
    assert store.budget.used == 0
    assert store.client.delete_object.call_count == 2


def test_failed_delete_proof_and_corrupt_readback_never_refund(physical_store, tmp_path):
    store, contents = physical_store
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    store.put('member', path)
    store.client.delete_object.side_effect = lambda **kwargs: {}
    with pytest.raises(ValueError, match='could not be verified'):
        store.delete('member')
    assert store.budget.used == 7
    contents['private', 'backups/member'] = b'corrupt'
    with pytest.raises(ValueError, match='SHA-256'):
        store.delete('member')
    assert store.budget.used == 7
    assert store.client.delete_object.call_count == 1


def test_preledger_deletion_never_refunds_baseline_storage(physical_store):
    store, contents = physical_store
    contents['private', 'backups/old'] = b'old'
    store.budget.used = 100
    store.delete('old')
    assert store.budget.used == 100
    assert store.budget.receipts == {}


def test_real_ledger_keeps_failed_write_bytes_and_refunds_only_verified_deletion(ledger, physical_store, tmp_path):
    pool, window = ledger
    store, contents = physical_store
    store.budget = Budget(pool, window)
    store.charge = store.budget.result_charge
    path = tmp_path / 'payload'
    path.write_bytes(b'payload')
    original = store.client.put_object.side_effect
    def lost(**kwargs):
        original(**kwargs)
        raise ReadTimeoutError(endpoint_url=ENDPOINT)
    store.client.put_object.side_effect = lost
    with pytest.raises(ReadTimeoutError):
        store.put('member', path)
    with pool.connection() as connection:
        assert connection.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 107
    store.put('member', path)
    assert store.client.put_object.call_count == 1
    store.delete('member')
    with pool.connection() as connection:
        assert connection.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100
        assert connection.execute('SELECT state FROM research.budget_object_receipts').fetchone()['state'] == 'deleted'
