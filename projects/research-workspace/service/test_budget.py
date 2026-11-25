"""Real-transaction resource bounds, rollback and role separation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
import pytest

from service.budget import (Budget, BudgetUnavailable, METRICS, REQUEST_OVERHEAD_BYTES,
    FINAL_OBJECT_BYTES, MIGRATION_COPIES, authorize_launch, debit, install_window, reserve_migration,
    reserve_search, retire_run, search_liability)


@pytest.fixture
def ledger():
    url = os.environ.get('RESEARCH_BUDGET_TEST_DATABASE_URL') or os.environ.get('RESEARCH_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set an isolated migrated PostgreSQL test database')
    identity = 'budget-' + uuid.uuid4().hex
    with ConnectionPool(url, min_size=1, max_size=6, kwargs={'row_factory': dict_row}) as pool:
        with pool.connection() as conn:
            name = conn.execute('SELECT current_database() AS name').fetchone()['name']
            assert name.endswith('_test'), 'Refusing a non-test database'
            assert conn.execute('SELECT count(*) AS n FROM research.budget_windows').fetchone()['n'] == 0
            now = datetime.now(timezone.utc)
            limits = dict.fromkeys(METRICS, 10**12)
            limits.update(search_attempts=6, service_invocations=20, r2_class_a=10, r2_class_b=10)
            install_window(conn, identity, now-timedelta(minutes=1), now+timedelta(days=1), limits,
                           {'service_invocations': 3}, 10**12, 100, 'fixture reviewed allowance')
        try:
            yield pool, identity
        finally:
            with pool.connection() as conn:
                conn.execute('DELETE FROM research.budget_search_receipts')
                conn.execute('DELETE FROM research.budget_migration_receipts')
                conn.execute('DELETE FROM research.budget_object_receipts')
                conn.execute('DELETE FROM research.budget_metrics')
                conn.execute('DELETE FROM research.budget_windows')
                conn.execute('DELETE FROM research.budget_storage')


def usage(pool, identity, metric):
    with pool.connection() as conn:
        return conn.execute('SELECT used FROM research.budget_metrics WHERE window_id=%s AND metric=%s',
                            (identity, metric)).fetchone()['used']


def test_concurrent_replicas_cannot_overdraw_remaining_allowance(ledger):
    pool, identity = ledger
    budget = Budget(pool, identity)
    def invoke(_):
        try:
            budget.invocation()
            return True
        except BudgetUnavailable:
            return False
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(invoke, range(40)))
    assert sum(results) == 17
    assert usage(pool, identity, 'service_invocations') == 20


def test_multi_metric_failure_rolls_back_all_debits(ledger):
    pool, identity = ledger
    with pytest.raises(BudgetUnavailable):
        Budget(pool, identity).charge(api_response_bytes=20, r2_class_a=11)
    assert usage(pool, identity, 'api_response_bytes') == 0
    assert usage(pool, identity, 'r2_class_a') == 0


def test_egress_and_failed_put_are_committed_before_network(ledger):
    pool, identity = ledger
    budget = Budget(pool, identity)
    with pytest.raises(RuntimeError), pool.connection() as conn:
        conn.execute('SELECT 1')
        budget.result_charge('put', bytes=1024)
        assert usage(pool, identity, 'r2_class_a') == 1
        raise RuntimeError('network acknowledgement lost and caller rolled back')
    assert usage(pool, identity, 'r2_class_a') == 1
    assert usage(pool, identity, 'google_egress_bytes') == REQUEST_OVERHEAD_BYTES + 1024


@pytest.mark.parametrize('change', ["armed=false", "ends_at=clock_timestamp()-interval '1 second'",
                                  "authority_database='another_restore_test'"])
def test_disarmed_expired_and_copied_windows_fail_closed(ledger, change):
    pool, identity = ledger
    with pool.connection() as conn:
        conn.execute('UPDATE research.budget_windows SET ' + change)
    with pytest.raises(BudgetUnavailable):
        Budget(pool, identity).invocation()
    assert usage(pool, identity, 'service_invocations') == 3


def test_missing_allowance_and_unknown_operations_do_not_make_calls(ledger):
    pool, identity = ledger
    with pytest.raises(BudgetUnavailable):
        Budget(pool, 'not-reviewed').invocation()
    with pytest.raises(BudgetUnavailable):
        Budget(pool, identity).result_charge('unpriced-operation')
    assert usage(pool, identity, 'r2_class_a') == 0


def test_runtime_roles_can_only_debit_permitted_metrics(ledger):
    pool, identity = ledger
    with pool.connection() as conn:
        conn.execute('SET LOCAL ROLE research_api')
        debit(conn, identity, {'r2_class_b': 1})
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            debit(conn, identity, {'r2_class_a': 1})
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute('UPDATE research.budget_metrics SET used=0')
    assert usage(pool, identity, 'r2_class_b') == 1
    assert usage(pool, identity, 'r2_class_a') == 0


@pytest.mark.parametrize('group,allowed,denied', [
    ('research_api', 'r2_class_b', 'r2_class_a'),
    ('research_worker', 'r2_class_a', 'search_attempts'),
    ('research_budget_operator', 'operator_executions', None),
])
def test_authenticated_restricted_logins_cannot_edit_allowances(ledger, group, allowed, denied):
    pool, identity = ledger
    role = 'budget_fixture_' + uuid.uuid4().hex[:16]
    password = 'local-test-fixture-' + uuid.uuid4().hex
    with pool.connection() as conn:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}').format(
            sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(group), sql.Identifier(role)))
    options = conninfo_to_dict(pool.conninfo)
    options.update(user=role, password=password)
    try:
        with psycopg.connect(**options, row_factory=dict_row) as conn:
            assert conn.execute('SELECT session_user AS name').fetchone()['name'] == role
            debit(conn, identity, {allowed: 1})
            if denied:
                with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    debit(conn, identity, {denied: 1})
            for statement in ('UPDATE research.budget_metrics SET used=0',
                              'UPDATE research.budget_windows SET armed=true',
                              'UPDATE research.publications SET status=\'published\''):
                with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    conn.execute(statement)
        assert usage(pool, identity, allowed) == 1
    finally:
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@contextmanager
def query(pool):
    identity = 'budget-query-' + uuid.uuid4().hex
    with pool.connection() as conn:
        conn.execute("INSERT INTO research.datasets VALUES(%s,'Fixture','', 'fixture','[]',NULL)", (identity,))
        conn.execute("""INSERT INTO research.publications(id,dataset_id,snapshot_id,status,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint,published_at)
            VALUES(%s,%s,'snapshot','published','{}','{}','source','bundle','schema',now())""", (identity, identity))
        conn.execute("INSERT INTO research.rulesets(publication_id,version,rules) VALUES(%s,'fixture','{}')", (identity,))
        conn.execute("""INSERT INTO research.query_runs(id,publication_id,ruleset_version,solver_version,query_hash,definition,dispatch_generation)
            VALUES(%s,%s,'fixture','fixture',%s,'{}',1)""", (identity, identity, identity))
    try:
        yield identity
    finally:
        with pool.connection() as conn:
            conn.execute('DELETE FROM research.query_runs WHERE id=%s', (identity,))
            conn.execute('DELETE FROM research.rulesets WHERE publication_id=%s', (identity,))
            conn.execute('DELETE FROM research.publications WHERE id=%s', (identity,))
            conn.execute('DELETE FROM research.datasets WHERE id=%s', (identity,))


def test_query_reserves_all_attempts_and_retained_copies_once(ledger):
    pool, identity = ledger
    with query(pool) as run:
        with pool.connection() as conn:
            reserve_search(conn, identity, run, 1, 3)
            reserve_search(conn, identity, run, 1, 3)
        assert usage(pool, identity, 'search_attempts') == 3
        with pool.connection() as conn:
            assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100 + search_liability(3)
            assert search_liability(3) == 1_302_653_184
            conn.execute('SET LOCAL ROLE research_dispatcher')
            authorize_launch(conn, identity, run, 1, 3)
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            authorize_launch(conn, identity, run, 1, 4)
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_search(conn, identity, run, 1, 2)


def test_query_rollback_preserves_both_resource_balances(ledger):
    pool, identity = ledger
    with query(pool) as run:
        with pytest.raises(RuntimeError), pool.connection() as conn:
            reserve_search(conn, identity, run, 1, 3)
            raise RuntimeError('acceptance transaction failed')
        assert usage(pool, identity, 'search_attempts') == 0
        with pool.connection() as conn:
            assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100


def test_storage_failure_does_not_consume_compute_allowance(ledger):
    pool, identity = ledger
    with query(pool) as run:
        with pool.connection() as conn:
            conn.execute('UPDATE research.budget_storage SET ceiling=used')
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_search(conn, identity, run, 1, 3)
        assert usage(pool, identity, 'search_attempts') == 0


def test_window_reinstall_cannot_reset_usage(ledger):
    pool, identity = ledger
    with pytest.raises(psycopg.errors.UniqueViolation), pool.connection() as conn:
        now = datetime.now(timezone.utc)
        install_window(conn, identity, now, now+timedelta(days=1), dict.fromkeys(METRICS, 10**12),
                       {}, 10**12, 0, 'attempted baseline reset')
    assert usage(pool, identity, 'service_invocations') == 3


def test_api_body_bytes_share_the_google_output_allowance(ledger):
    pool, identity = ledger
    Budget(pool, identity).response(500)
    assert usage(pool, identity, 'api_response_bytes') == 500
    assert usage(pool, identity, 'google_egress_bytes') == 500 + REQUEST_OVERHEAD_BYTES


@contextmanager
def staged_migration(pool):
    with query(pool) as run:
        fingerprint = 'b' * 64
        generation = hashlib.sha256(b'research-result-migration-v1\0' + run.encode() + b'\0' + fingerprint.encode()).hexdigest()[:32]
        with pool.connection() as conn:
            conn.execute('INSERT INTO research.result_generations(id,run_id,source_fingerprint) VALUES(%s,%s,%s)',
                         (generation, run, psycopg.types.json.Jsonb({'sha256': fingerprint})))
        yield generation


def test_migration_storage_reserves_full_retention_once_and_survives_generation_removal(ledger):
    pool, window = ledger
    with staged_migration(pool) as generation:
        with pool.connection() as conn:
            conn.execute('SET LOCAL ROLE research_budget_operator')
            reserve_migration(conn, window, generation, 12345)
            reserve_migration(conn, window, generation, 12345)
        with pool.connection() as conn:
            receipt = conn.execute('SELECT * FROM research.budget_migration_receipts WHERE generation_id=%s', (generation,)).fetchone()
            assert receipt['retained_object_bytes'] == 12345
            assert receipt['liability_bytes'] == MIGRATION_COPIES * 12345
            assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100 + MIGRATION_COPIES * 12345
    with pool.connection() as conn:
        assert conn.execute('SELECT liability_bytes FROM research.budget_migration_receipts WHERE generation_id=%s',
                            (generation,)).fetchone()['liability_bytes'] == MIGRATION_COPIES * 12345


def test_migration_storage_exhaustion_and_transaction_rollback_leave_no_receipt(ledger):
    pool, window = ledger
    with staged_migration(pool) as generation:
        with pytest.raises(RuntimeError), pool.connection() as conn:
            reserve_migration(conn, window, generation, 12345)
            raise RuntimeError('caller transaction failed')
        with pool.connection() as conn:
            assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100
            assert conn.execute('SELECT 1 FROM research.budget_migration_receipts').fetchone() is None
            conn.execute('UPDATE research.budget_storage SET ceiling=used')
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_migration(conn, window, generation, 12345)
        with pool.connection() as conn:
            assert conn.execute('SELECT 1 FROM research.budget_migration_receipts').fetchone() is None


def test_migration_receipt_rejects_changed_bytes_fingerprint_and_expired_window(ledger):
    pool, window = ledger
    with staged_migration(pool) as generation:
        with pool.connection() as conn:
            reserve_migration(conn, window, generation, 12345)
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_migration(conn, window, generation, 12346)
        with pool.connection() as conn:
            conn.execute('UPDATE research.result_generations SET source_fingerprint=%s WHERE id=%s',
                         (psycopg.types.json.Jsonb({'sha256': 'c' * 64}), generation))
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_migration(conn, window, generation, 12345)
        with pool.connection() as conn:
            conn.execute('UPDATE research.result_generations SET source_fingerprint=%s WHERE id=%s',
                         (psycopg.types.json.Jsonb({'sha256': 'b' * 64}), generation))
            conn.execute("UPDATE research.budget_windows SET ends_at=clock_timestamp()-interval '1 second'")
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_migration(conn, window, generation, 12345)


@pytest.mark.parametrize('role', ['research_api', 'research_worker', 'research_dispatcher'])
def test_runtime_roles_cannot_reserve_migration_liability(ledger, role):
    pool, window = ledger
    with staged_migration(pool) as generation:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), pool.connection() as conn:
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
            reserve_migration(conn, window, generation, 12345)


@pytest.mark.parametrize('amount', [0, -1, True, 0.5, FINAL_OBJECT_BYTES + 1])
def test_invalid_migration_storage_bounds_fail_before_sql(ledger, amount):
    pool, window = ledger
    with pytest.raises(BudgetUnavailable), pool.connection() as conn:
        reserve_migration(conn, window, 'a' * 32, amount)


def test_migration_reservation_requires_staged_fingerprint(ledger):
    pool, window = ledger
    with pytest.raises(BudgetUnavailable), pool.connection() as conn:
        reserve_migration(conn, window, 'a' * 32, 12345)


def test_migration_receipt_cannot_be_recharged_under_another_window(ledger):
    pool, window = ledger
    with staged_migration(pool) as generation:
        with pool.connection() as conn:
            reserve_migration(conn, window, generation, 12345)
            before = conn.execute('SELECT ceiling,used FROM research.budget_storage').fetchone()
            conn.execute('UPDATE research.budget_windows SET armed=false')
            now = datetime.now(timezone.utc)
            install_window(conn, 'next-window', now-timedelta(minutes=1), now+timedelta(days=1),
                           dict.fromkeys(METRICS, 10**12), {}, before['ceiling'], before['used'], 'reviewed next-window fixture')
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_migration(conn, 'next-window', generation, 12345)
        with pool.connection() as conn:
            assert conn.execute('SELECT ceiling,used FROM research.budget_storage').fetchone() == before


def test_concurrent_migration_reservations_share_one_receipt(ledger):
    pool, window = ledger
    with staged_migration(pool) as generation:
        def reserve(_):
            with pool.connection() as conn:
                reserve_migration(conn, window, generation, 12345)
        with ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(reserve, range(12)))
        with pool.connection() as conn:
            assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100 + MIGRATION_COPIES * 12345
            assert conn.execute('SELECT count(*) AS n FROM research.budget_migration_receipts').fetchone()['n'] == 1


OBJECT_ID = hashlib.sha256(b'https://account.r2.cloudflarestorage.com\nbucket\nbackup/object').hexdigest()
OBJECT_SHA = hashlib.sha256(b'fixture').hexdigest()


def stored_bytes(pool):
    with pool.connection() as conn:
        return conn.execute('SELECT used FROM research.budget_storage').fetchone()['used']


def test_physical_intent_survives_caller_failure_and_is_never_double_reserved(ledger):
    pool, window = ledger
    budget = Budget(pool, window)
    with pytest.raises(RuntimeError), pool.connection() as caller:
        caller.execute('SELECT 1')
        assert budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA) == 'new'
        assert stored_bytes(pool) == 12445
        raise RuntimeError('write acknowledgement is uncertain')
    assert budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA) == 'planned'
    assert budget.get_object_receipt(OBJECT_ID) == dict(identity=OBJECT_ID, window_id=window,
                                                      size=12345, sha256=OBJECT_SHA, state='planned')
    with pytest.raises(BudgetUnavailable):
        budget.delete_object(OBJECT_ID)
    assert stored_bytes(pool) == 12445


def test_physical_receipt_full_lifecycle_refunds_only_once_and_forbids_key_reuse(ledger):
    pool, window = ledger
    budget = Budget(pool, window)
    assert budget.get_object_receipt(OBJECT_ID) is None
    with pytest.raises(BudgetUnavailable):
        budget.verify_object(OBJECT_ID)
    assert budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA) == 'new'
    budget.verify_object(OBJECT_ID)
    budget.verify_object(OBJECT_ID)
    assert budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA) == 'verified'
    assert budget.delete_object(OBJECT_ID) == 12345
    assert budget.delete_object(OBJECT_ID) == 0
    assert stored_bytes(pool) == 100
    assert budget.get_object_receipt(OBJECT_ID)['state'] == 'deleted'
    with pytest.raises(BudgetUnavailable):
        budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA)
    with pytest.raises(BudgetUnavailable):
        budget.verify_object(OBJECT_ID)


@pytest.mark.parametrize('size,checksum', [(12346, OBJECT_SHA), (12345, 'c' * 64)])
def test_physical_identity_conflicts_never_consume_more_storage(ledger, size, checksum):
    pool, window = ledger
    budget = Budget(pool, window)
    budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA)
    with pytest.raises(BudgetUnavailable):
        budget.prepare_object(OBJECT_ID, size, checksum)
    assert stored_bytes(pool) == 12445


def test_physical_storage_ceiling_rejects_before_creation_and_zero_byte_objects_work(ledger):
    pool, window = ledger
    budget = Budget(pool, window)
    with pool.connection() as conn:
        conn.execute('UPDATE research.budget_storage SET ceiling=used')
    with pytest.raises(BudgetUnavailable):
        budget.prepare_object(OBJECT_ID, 1, OBJECT_SHA)
    assert budget.get_object_receipt(OBJECT_ID) is None
    assert budget.prepare_object(OBJECT_ID, 0, hashlib.sha256(b'').hexdigest()) == 'new'
    budget.verify_object(OBJECT_ID)
    assert budget.delete_object(OBJECT_ID) == 0
    assert budget.get_object_receipt(OBJECT_ID)['state'] == 'deleted'


def test_concurrent_physical_creators_and_deleters_share_one_reservation(ledger):
    pool, window = ledger
    budget = Budget(pool, window)
    with ThreadPoolExecutor(max_workers=6) as workers:
        states = list(workers.map(lambda _: budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA), range(18)))
    assert states.count('new') == 1 and states.count('planned') == 17
    assert stored_bytes(pool) == 12445
    budget.verify_object(OBJECT_ID)
    with ThreadPoolExecutor(max_workers=6) as workers:
        refunds = list(workers.map(lambda _: budget.delete_object(OBJECT_ID), range(18)))
    assert sum(refunds) == 12345 and refunds.count(0) == 17
    assert stored_bytes(pool) == 100


def test_physical_liability_survives_window_rollover_and_reconciles_under_current_authority(ledger):
    pool, window = ledger
    prior = Budget(pool, window)
    prior.prepare_object(OBJECT_ID, 12345, OBJECT_SHA)
    with pool.connection() as conn:
        conn.execute('UPDATE research.budget_windows SET armed=false')
        state = conn.execute('SELECT ceiling,used FROM research.budget_storage').fetchone()
        now = datetime.now(timezone.utc)
        install_window(conn, 'next-window', now-timedelta(minutes=1), now+timedelta(days=1),
                       dict.fromkeys(METRICS, 10**12), {}, state['ceiling'], state['used'], 'reviewed rollover fixture')
    current = Budget(pool, 'next-window')
    with pytest.raises(BudgetUnavailable):
        prior.verify_object(OBJECT_ID)
    assert current.prepare_object(OBJECT_ID, 12345, OBJECT_SHA) == 'planned'
    assert current.get_object_receipt(OBJECT_ID)['window_id'] == window
    assert stored_bytes(pool) == 12445
    current.verify_object(OBJECT_ID)
    assert current.delete_object(OBJECT_ID) == 12345
    assert stored_bytes(pool) == 100


@pytest.mark.parametrize('role', ['research_api', 'research_worker', 'research_dispatcher'])
def test_runtime_roles_cannot_read_change_or_refund_operator_storage_receipts(ledger, role):
    pool, window = ledger
    budget = Budget(pool, window)
    budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA)
    budget.verify_object(OBJECT_ID)
    statements = (
        ('SELECT research.budget_prepare_object(%s,%s,%s,%s)', (window, OBJECT_ID, 12345, OBJECT_SHA)),
        ('SELECT research.budget_get_object_receipt(%s,%s)', (window, OBJECT_ID)),
        ('SELECT research.budget_verify_object(%s,%s)', (window, OBJECT_ID)),
        ('SELECT research.budget_delete_object(%s,%s)', (window, OBJECT_ID)),
        ('SELECT research.budget_retire_run(%s,%s)', (window, 'absent')),
        ('UPDATE research.budget_object_receipts SET state=\'deleted\'', ()),
        ('DELETE FROM research.budget_object_receipts', ()),
    )
    for statement, args in statements:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), pool.connection() as conn:
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
            conn.execute(statement, args)
    assert stored_bytes(pool) == 12445


def test_retired_run_releases_query_and_migration_liability_atomically_without_refunding_attempts(ledger):
    pool, window = ledger
    budget = Budget(pool, window)
    budget.prepare_object(OBJECT_ID, 12345, OBJECT_SHA)
    with staged_migration(pool) as generation:
        with pool.connection() as conn:
            run = conn.execute('SELECT run_id FROM research.result_generations WHERE id=%s', (generation,)).fetchone()['run_id']
            reserve_search(conn, window, run, 1, 3)
            reserve_migration(conn, window, generation, 2000)
            conn.execute("UPDATE research.query_runs SET state='complete',expires_at=clock_timestamp()-interval '1 day',result_generation_id=%s WHERE id=%s", (generation, run))
        total = search_liability(3) + MIGRATION_COPIES * 2000
        assert budget.retire_run(run) == 0
        with pytest.raises(RuntimeError), pool.connection() as conn:
            conn.execute('UPDATE research.query_runs SET result_generation_id=NULL WHERE id=%s', (run,))
            conn.execute('DELETE FROM research.result_generations WHERE id=%s', (generation,))
            assert retire_run(conn, window, run) == total
            raise RuntimeError('cleanup transaction failed')
        assert stored_bytes(pool) == 12445 + total
        with pool.connection() as conn:
            conn.execute('UPDATE research.query_runs SET result_generation_id=NULL WHERE id=%s', (run,))
            conn.execute('DELETE FROM research.result_generations WHERE id=%s', (generation,))
            assert retire_run(conn, window, run) == total
        assert budget.retire_run(run) == 0
        assert stored_bytes(pool) == 12445
        assert usage(pool, window, 'search_attempts') == 3
        with pytest.raises(BudgetUnavailable), pool.connection() as conn:
            reserve_search(conn, window, run, 1, 3)
        with pool.connection() as conn:
            conn.execute("UPDATE research.query_runs SET state='queued',expires_at=NULL,dispatch_generation=2 WHERE id=%s", (run,))
            reserve_search(conn, window, run, 2, 3)
        assert usage(pool, window, 'search_attempts') == 6
        assert stored_bytes(pool) == 12445 + search_liability(3)


@pytest.mark.parametrize('change', ["state='queued'", "expires_at=NULL", "expires_at=clock_timestamp()+interval '1 day'",
                                  "lease_owner='worker'", "lease_until=clock_timestamp()-interval '1 day'"])
def test_run_retirement_requires_terminal_expired_and_completely_unleased(ledger, change):
    pool, window = ledger
    with query(pool) as run:
        with pool.connection() as conn:
            reserve_search(conn, window, run, 1, 3)
            conn.execute("UPDATE research.query_runs SET state='failed',expires_at=clock_timestamp()-interval '1 day' WHERE id=%s", (run,))
            conn.execute('UPDATE research.query_runs SET ' + change + ' WHERE id=%s', (run,))
        assert Budget(pool, window).retire_run(run) == 0
        assert stored_bytes(pool) == 100 + search_liability(3)


def test_uncertain_execution_and_retained_generation_prevent_run_refunds(ledger):
    pool, window = ledger
    with query(pool) as run:
        with pool.connection() as conn:
            reserve_search(conn, window, run, 1, 3)
            conn.execute("UPDATE research.query_runs SET state='cancelled',expires_at=clock_timestamp()-interval '1 day' WHERE id=%s", (run,))
            conn.execute("INSERT INTO research.execution_reservations(id,run_id,generation,state) VALUES('pending-copy-proof',%s,1,'uncertain')", (run,))
        assert Budget(pool, window).retire_run(run) == 0
        with pool.connection() as conn:
            conn.execute("UPDATE research.execution_reservations SET state='terminal' WHERE run_id=%s", (run,))
            conn.execute('INSERT INTO research.result_generations(id,run_id) VALUES(%s,%s)', ('a' * 32, run))
        assert Budget(pool, window).retire_run(run) == 0
        with pool.connection() as conn:
            conn.execute('DELETE FROM research.result_generations WHERE run_id=%s', (run,))
            assert retire_run(conn, window, run) == search_liability(3)
        assert stored_bytes(pool) == 100
