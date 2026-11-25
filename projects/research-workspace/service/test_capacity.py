"""Qualified on-demand measurements, limited privileges and reservation races."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time
from unittest.mock import Mock, patch

import psycopg
from psycopg import sql
import pytest

from service.admission import reserve
from service.capacity import refresh
from service.config import CapacityUnavailable, Settings
from service.test_service import service, submission


@pytest.fixture
def collector(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    with pool.connection() as conn:
        original = conn.execute("SELECT pg_get_functiondef('research.measure_neon_branch()'::regprocedure) AS ddl").fetchone()['ddl']
        previous = conn.execute('SELECT * FROM research.capacity_sample').fetchone()
        conn.execute('DELETE FROM research.capacity_sample')
    try:
        yield pool, replace(settings, capacity_measurement_mode='neon_branch', capacity_limit_bytes=1000,
                            result_reservation_bytes=100, database_headroom_bytes=100), run_id
    finally:
        with pool.connection() as conn:
            conn.execute(original)
            conn.execute('DELETE FROM research.capacity_sample')
            if previous:
                conn.execute('INSERT INTO research.capacity_sample VALUES(true,%s,%s,%s)',
                             (previous['used_bytes'], previous['limit_bytes'], previous['measured_at']))


def measurement(pool, body='SELECT 200::bigint,1000::bigint'):
    with pool.connection() as conn:
        conn.execute(sql.SQL('''CREATE OR REPLACE FUNCTION research.measure_neon_branch()
            RETURNS TABLE(used_bytes bigint,limit_bytes bigint) LANGUAGE sql SET search_path=pg_catalog AS {}''').format(sql.Literal(body)))


def sample(pool):
    with pool.connection() as conn:
        return conn.execute('SELECT * FROM research.capacity_sample').fetchone()


def test_manual_mode_never_calls_a_collector():
    conn = Mock()
    refresh(conn, Settings())
    conn.execute.assert_not_called()


def test_neon_mode_and_reviewed_limit_require_explicit_qualification():
    for mode, limit in [('unknown', 1000), ('neon_branch', 1073741825), ('neon_branch', True), ('neon_branch', 0)]:
        conn = Mock()
        with pytest.raises(CapacityUnavailable):
            refresh(conn, Settings(capacity_measurement_mode=mode, capacity_limit_bytes=limit))
        conn.execute.assert_not_called()
    with patch.dict('os.environ', {'RESEARCH_CAPACITY_MEASUREMENT_MODE': 'neon_branch', 'RESEARCH_CAPACITY_LIMIT_BYTES': '1073741825'}):
        with pytest.raises(ValueError, match='qualified'):
            Settings.from_env()


def test_generic_postgres_cannot_refresh_even_with_placeholder_neon_settings(collector):
    pool, settings, _ = collector
    with pool.connection() as conn, pytest.raises(CapacityUnavailable, match='cannot be measured'):
        conn.execute("SET LOCAL neon.max_cluster_size='1GB'")
        conn.execute("SET LOCAL neon.tenant_id='00000000000000000000000000000000'")
        conn.execute("SET LOCAL neon.timeline_id='00000000000000000000000000000000'")
        refresh(conn, settings)
    assert sample(pool) is None


def test_api_refreshes_at_admission_and_reservations_still_accumulate(collector):
    pool, settings, run_id = collector
    measurement(pool)
    with pool.connection() as conn:
        conn.execute('SET LOCAL ROLE research_api')
        reserve(conn, run_id, settings)
        first = conn.execute('SELECT * FROM research.capacity_sample').fetchone()
        assert first['used_bytes'] == 200 and first['limit_bytes'] == 1000
        assert first['measured_at'] > conn.execute('SELECT now() AS stamp').fetchone()['stamp']
    with pool.connection() as conn:
        conn.execute('SET LOCAL ROLE research_api')
        reserve(conn, run_id, settings)
        assert conn.execute('SELECT bytes FROM research.capacity_reservations WHERE run_id=%s', (run_id,)).fetchone()['bytes'] == 200
    assert sample(pool)['measured_at'] > first['measured_at']


@pytest.mark.parametrize('actual,reviewed,expected', [(800,1000,800),(1000,700,700)])
def test_effective_ceiling_uses_lower_actual_or_reviewed_limit(collector, actual, reviewed, expected):
    pool, settings, _ = collector
    measurement(pool, f'SELECT 200::bigint,{actual}::bigint')
    with pool.connection() as conn:
        refresh(conn, replace(settings, capacity_limit_bytes=reviewed))
    assert sample(pool)['limit_bytes'] == expected


@pytest.mark.parametrize('role', ['research_worker','research_dispatcher','service_role','research_budget_operator'])
def test_only_api_can_execute_collector(collector, role):
    pool, settings, _ = collector
    measurement(pool)
    with pool.connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
        conn.execute('SELECT research.refresh_neon_capacity(%s)', (settings.capacity_limit_bytes,))
    assert sample(pool) is None


def test_api_cannot_invent_measurements_or_call_private_measurement_helper(collector):
    pool, _, _ = collector
    for statement in ['SELECT * FROM research.measure_neon_branch()',
                      'INSERT INTO research.capacity_sample VALUES(true,0,1000,now())']:
        with pool.connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SET LOCAL ROLE research_api')
            conn.execute(statement)


@pytest.mark.parametrize('body', ['SELECT NULL::bigint,1000::bigint', 'SELECT 0::bigint,0::bigint',
                               'SELECT (-1)::bigint,1000::bigint', 'SELECT (1/0)::bigint,1000::bigint'])
def test_failed_measurement_never_falls_back_to_previous_sample(collector, body):
    pool, settings, run_id = collector
    with pool.connection() as conn:
        conn.execute('INSERT INTO research.capacity_sample VALUES(true,0,1000,now())')
    before = sample(pool)
    measurement(pool, body)
    with pool.connection() as conn, pytest.raises(CapacityUnavailable):
        reserve(conn, run_id, settings)
    assert sample(pool) == before
    with pool.connection() as conn:
        assert conn.execute('SELECT 1 FROM research.capacity_reservations WHERE run_id=%s', (run_id,)).fetchone() is None


def test_future_sample_cannot_be_replaced_by_an_older_measurement(collector):
    pool, settings, _ = collector
    measurement(pool)
    with pool.connection() as conn:
        conn.execute("INSERT INTO research.capacity_sample VALUES(true,50,1000,now()+interval '1 day')")
    before = sample(pool)
    with pool.connection() as conn, pytest.raises(CapacityUnavailable):
        refresh(conn, settings)
    assert sample(pool) == before


def test_terminal_reservations_release_only_after_proven_execution_completion(collector):
    pool, settings, run_id = collector
    measurement(pool)
    with pool.connection() as conn:
        conn.execute('INSERT INTO research.capacity_reservations VALUES(%s,100)', (run_id,))
        conn.execute("UPDATE research.query_runs SET state='complete',finished_at=now() WHERE id=%s", (run_id,))
        conn.execute("INSERT INTO research.execution_reservations(id,run_id,generation,state) VALUES(%s,%s,1,'uncertain')", (run_id,run_id))
    with pool.connection() as conn:
        refresh(conn, settings)
        assert conn.execute('SELECT bytes FROM research.capacity_reservations WHERE run_id=%s', (run_id,)).fetchone()['bytes'] == 100
        conn.execute("UPDATE research.execution_reservations SET state='terminal' WHERE id=%s", (run_id,))
    with pool.connection() as conn:
        refresh(conn, settings)
        assert conn.execute('SELECT 1 FROM research.capacity_reservations WHERE run_id=%s', (run_id,)).fetchone() is None


def test_completion_during_measurement_retains_reservation_until_next_measurement(collector):
    pool, settings, run_id = collector
    lock_id = 28764109
    measurement(pool, f'SELECT 200::bigint,1000::bigint FROM (SELECT pg_advisory_xact_lock({lock_id})) barrier')
    with pool.connection() as conn:
        conn.execute('INSERT INTO research.capacity_reservations VALUES(%s,100)', (run_id,))
        conn.execute("UPDATE research.query_runs SET state='running' WHERE id=%s", (run_id,))
    with pool.connection() as barrier, ThreadPoolExecutor(max_workers=1) as threads:
        barrier.execute('SELECT pg_advisory_lock(%s)', (lock_id,))
        def collect():
            with pool.connection() as conn:
                conn.execute('SET LOCAL ROLE research_api')
                refresh(conn, settings)
        pending = threads.submit(collect)
        try:
            for _ in range(100):
                if barrier.execute("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=%s AND NOT granted", (lock_id,)).fetchone():
                    break
                time.sleep(.01)
            else:
                pytest.fail('Collector did not reach the measurement barrier')
            with pool.connection() as conn:
                conn.execute("UPDATE research.query_runs SET state='complete',finished_at=now()-interval '1 second' WHERE id=%s", (run_id,))
        finally:
            barrier.execute('SELECT pg_advisory_unlock(%s)', (lock_id,))
        pending.result(timeout=5)
    with pool.connection() as conn:
        assert conn.execute('SELECT bytes FROM research.capacity_reservations WHERE run_id=%s', (run_id,)).fetchone()['bytes'] == 100
        refresh(conn, settings)
        assert conn.execute('SELECT 1 FROM research.capacity_reservations WHERE run_id=%s', (run_id,)).fetchone() is None


def test_measured_usage_cannot_overcommit_remaining_storage(collector):
    pool, settings, run_id = collector
    measurement(pool, 'SELECT 801::bigint,1000::bigint')
    with pytest.raises(CapacityUnavailable, match='capacity'), pool.connection() as conn:
        reserve(conn, run_id, settings)
    assert sample(pool) is None
