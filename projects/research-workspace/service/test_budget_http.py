"""HTTP effects are gated before work and before any application response bytes."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout
import pytest

from service.app import create_app
from service.admission import record_sample
from service.budget import BudgetUnavailable, METRICS, install_window, search_liability
from service.budget_http import BudgetHTTPMiddleware, MAX_RESPONSE_BYTES
from service.config import Settings
from service.test_dispatch import FakeMarkers


class Allowance:
    def __init__(self, *, invocation_error=None, response_error=None, response_limit=MAX_RESPONSE_BYTES):
        self.invocation_error = invocation_error
        self.response_error = response_error
        self.response_limit = response_limit
        self.calls = []
        self.used = 0
        self.threads = []

    def invocation(self):
        self.calls.append(('invocation',))
        self.threads.append(threading.get_ident())
        if self.invocation_error is not None:
            raise self.invocation_error

    def response(self, size):
        self.calls.append(('response', size))
        self.threads.append(threading.get_ident())
        if self.response_error is not None:
            raise self.response_error
        if size > self.response_limit - self.used:
            raise BudgetUnavailable('sensitive internal allowance detail')
        self.used += size


def scope(method='GET', path='/data', headers=()):
    return dict(type='http', method=method, path=path, headers=list(headers), query_string=b'',
                scheme='https', http_version='1.1', server=('test', 443), client=('test', 1))


async def request(app, budget, *, request_scope=None, send=None, **options):
    sent = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def record(message):
        sent.append(message)
        if send:
            await send(message)
    middleware = BudgetHTTPMiddleware(app, state=SimpleNamespace(budget=budget), **options)
    await middleware(request_scope or scope(), receive, record)
    return sent


def responding(chunks, *, status=200):
    async def application(scope, receive, send):
        await send({'type': 'http.response.start', 'status': status, 'headers': [(b'x-original', b'private')]})
        for index, chunk in enumerate(chunks):
            await send({'type': 'http.response.body', 'body': chunk, 'more_body': index < len(chunks) - 1})
    return application


def test_exact_encoded_body_bytes_are_committed_before_any_response_event():
    budget = Allowance()
    chunks = ['é'.encode(), b'\xff', b'body']
    main_thread = threading.get_ident()
    async def sent(message):
        assert budget.calls == [('invocation',), ('response', 7)]
        assert budget.used == 7
    events = asyncio.run(request(responding(chunks), budget, send=sent))
    assert len(events) == 2
    assert events[-1]['body'] == b''.join(chunks)
    assert all(thread != main_thread for thread in budget.threads)


@pytest.mark.parametrize('error', [BudgetUnavailable('secret'), PoolTimeout('secret'), psycopg.errors.QueryCanceled('secret')])
def test_invocation_rejection_prevents_business_code_and_sanitizes_denial(error):
    budget = Allowance(invocation_error=error)
    async def must_not_run(*args):
        raise AssertionError('Business work must not start')
    events = asyncio.run(request(must_not_run, budget, request_scope=scope(headers=[(b'origin', b'https://allowed.test')]),
                                 allow_origins=['https://allowed.test']))
    assert events[0]['status'] == 503
    assert b'secret' not in events[1]['body']
    assert dict(events[0]['headers'])[b'access-control-allow-origin'] == b'https://allowed.test'
    assert budget.calls == [('invocation',)]


def test_response_allowance_failure_emits_only_small_denial_without_original_headers_or_bytes():
    budget = Allowance(response_limit=3)
    events = asyncio.run(request(responding([b'private-body']), budget))
    assert events[0]['status'] == 503
    assert b'x-original' not in dict(events[0]['headers'])
    assert b'private-body' not in events[1]['body']
    assert b'sensitive' not in events[1]['body']
    assert budget.used == 0
    assert budget.calls == [('invocation',), ('response', 12)]


def test_midstream_size_limit_never_releases_partial_body_or_charges_the_oversize_body():
    budget = Allowance()
    events = asyncio.run(request(responding([b'private', b'overflow']), budget, max_response_bytes=8))
    assert events[0]['status'] == 503
    assert b'private' not in events[1]['body']
    assert budget.calls == [('invocation',)]


def test_many_empty_chunks_use_one_bounded_body_and_one_final_response_event():
    budget = Allowance()
    events = asyncio.run(request(responding([b''] * 10000 + [b'ok']), budget))
    assert len(events) == 2 and events[-1]['body'] == b'ok'
    assert budget.used == 2


@pytest.mark.parametrize('after_complete', [False, True])
def test_programming_error_after_partial_or_complete_capture_is_not_silently_converted(after_complete):
    budget = Allowance()
    sent = []
    async def broken(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200})
        await send({'type': 'http.response.body', 'body': b'private', 'more_body': not after_complete})
        raise RuntimeError('application defect')
    async def collect(message):
        sent.append(message)
    with pytest.raises(RuntimeError, match='application defect'):
        asyncio.run(request(broken, budget, send=collect))
    assert sent == []
    assert budget.calls == [('invocation',)]


@pytest.mark.parametrize('phase', ['invocation', 'response'])
def test_arbitrary_ledger_programming_errors_are_not_changed_to_503(phase):
    budget = Allowance(**{phase + '_error': ValueError('programming defect')})
    with pytest.raises(ValueError, match='programming defect'):
        asyncio.run(request(responding([b'body']), budget))


def test_response_timeout_is_sanitized_before_emission():
    budget = Allowance(response_error=psycopg.errors.QueryCanceled('secret connection information'))
    events = asyncio.run(request(responding([b'private-body']), budget))
    assert events[0]['status'] == 503
    assert b'secret' not in events[-1]['body'] and b'private-body' not in events[-1]['body']


def test_transport_failure_does_not_refund_or_double_charge_already_authorized_body():
    budget = Allowance()
    async def failed_send(message):
        if message['type'] == 'http.response.body':
            raise OSError('client disconnected')
    with pytest.raises(OSError, match='disconnected'):
        asyncio.run(request(responding([b'body']), budget, send=failed_send))
    assert budget.calls == [('invocation',), ('response', 4)]
    assert budget.used == 4


@pytest.mark.parametrize('method,path,exempt', [('GET', '/live', True), ('HEAD', '/live', True),
    ('POST', '/live', False), ('OPTIONS', '/live', False), ('GET', '/ready', False), ('GET', '/health', False)])
def test_only_get_and_head_liveness_probes_bypass_budget(method, path, exempt):
    budget = Allowance(invocation_error=BudgetUnavailable())
    events = asyncio.run(request(responding([b'ok']), budget, request_scope=scope(method, path)))
    assert events[0]['status'] == (200 if exempt else 503)
    assert bool(budget.calls) is not exempt


def test_head_charges_and_emits_zero_body_bytes():
    budget = Allowance()
    events = asyncio.run(request(responding([b'would-be-get-body']), budget, request_scope=scope('HEAD')))
    assert events[-1]['body'] == b''
    assert budget.calls == [('invocation',), ('response', 0)]


def test_missing_required_budget_fails_closed_but_legacy_mode_is_unchanged():
    blocked = asyncio.run(request(responding([b'private']), None, required=True))
    allowed = asyncio.run(request(responding([b'legacy']), None))
    assert blocked[0]['status'] == 503 and b'private' not in blocked[-1]['body']
    assert allowed[-1]['body'] == b'legacy'


def test_streaming_framework_response_is_bounded_before_first_chunk_reaches_client():
    app = create_app(Settings())
    @app.get('/stream')
    def stream():
        return StreamingResponse(iter([b'x' * (MAX_RESPONSE_BYTES // 2),
                                       b'y' * (MAX_RESPONSE_BYTES // 2), b'overflow']))
    with TestClient(app) as client:
        allowance = Allowance()
        app.state.budget = allowance
        response = client.get('/stream')
    assert response.status_code == 503
    assert len(response.content) < 300
    assert allowance.calls == [('invocation',)]


def test_request_read_failure_propagates_with_only_invocation_charged():
    allowance = Allowance()
    emitted = []
    async def application(scope, receive, send):
        await receive()
    async def receive():
        raise OSError('disconnected request')
    async def send(message):
        emitted.append(message)
    middleware = BudgetHTTPMiddleware(application, state=SimpleNamespace(budget=allowance))
    with pytest.raises(OSError, match='disconnected request'):
        asyncio.run(middleware(scope('POST'), receive, send))
    assert emitted == []
    assert allowance.calls == [('invocation',)]


@pytest.fixture
def ledger_api(tmp_path):
    url = os.environ.get('RESEARCH_BUDGET_HTTP_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set RESEARCH_BUDGET_HTTP_TEST_DATABASE_URL to its dedicated migrated test database')
    identity = 'http-budget-' + uuid.uuid4().hex
    rules = dict(dataset_id=identity, snapshot_id='fixture', publication_id=identity, version='v1',
        units=[dict(form_id=str(index), unit_id=str(index), champion_id=str(index), name='Unit ' + str(index),
                    cost=index + 1, slots=1, locked=False, traits=['trait']) for index in range(3)],
        traits=[dict(trait_id='trait', name='Trait', breakpoints=[dict(breakpoint_id='two', minimum=2,
                     status='supported', evidence={})])])
    with ConnectionPool(url, min_size=1, max_size=1, timeout=2, kwargs={'row_factory': dict_row}) as pool:
        with pool.connection() as conn:
            database = conn.execute('SELECT current_database() AS name').fetchone()['name']
            assert database.startswith('research_budget_http') and database.endswith('_test')
            assert conn.execute('SELECT count(*) AS n FROM research.budget_windows').fetchone()['n'] == 0
            now = datetime.now(timezone.utc)
            install_window(conn, identity, now - timedelta(minutes=1), now + timedelta(days=1),
                           dict.fromkeys(METRICS, 10**12), {}, 10**12, 0, 'reviewed HTTP test allowance')
            conn.execute("INSERT INTO research.datasets(id,name,description,adapter,capabilities) VALUES(%s,'HTTP','Fixture','tft_set19','[]')", (identity,))
            conn.execute('''INSERT INTO research.publications(id,dataset_id,snapshot_id,status,manifest,scope,
                source_sha256,bundle_sha256,schema_fingerprint,published_at)
                VALUES(%s,%s,'fixture','published','{}','{}','fixture','fixture','fixture',now())''', (identity, identity))
            conn.execute('INSERT INTO research.rulesets(publication_id,version,rules) VALUES(%s,%s,%s)', (identity, 'v1', Jsonb(rules)))
            record_sample(conn, 0, 10**12)
            conn.execute('UPDATE research.dispatch_control SET paused=false WHERE singleton')
        settings = Settings(database_url=url, budget_window_id=identity, session_secret=identity,
                            deployment_mode='serverless', client_ip_mode='shared', max_launch_attempts=3)
        try:
            yield pool, settings, identity
        finally:
            with pool.connection() as conn:
                conn.execute('DELETE FROM research.query_subscriptions WHERE run_id IN (SELECT id FROM research.query_runs WHERE publication_id=%s)', (identity,))
                conn.execute('DELETE FROM research.query_runs WHERE publication_id=%s', (identity,))
                conn.execute('DELETE FROM research.rulesets WHERE publication_id=%s', (identity,))
                conn.execute('DELETE FROM research.publications WHERE id=%s', (identity,))
                conn.execute('DELETE FROM research.datasets WHERE id=%s', (identity,))
                conn.execute('DELETE FROM research.budget_search_receipts WHERE window_id=%s', (identity,))
                conn.execute('DELETE FROM research.budget_metrics WHERE window_id=%s', (identity,))
                conn.execute('DELETE FROM research.budget_windows WHERE id=%s', (identity,))
                conn.execute('DELETE FROM research.budget_storage')


def used(pool, window, metric):
    with pool.connection() as conn:
        return conn.execute('SELECT used FROM research.budget_metrics WHERE window_id=%s AND metric=%s', (window, metric)).fetchone()['used']


def test_real_request_debits_exact_error_and_success_bytes_and_probes_stay_idle(ledger_api):
    pool, settings, identity = ledger_api
    app = create_app(settings, pool, markers=FakeMarkers())
    with TestClient(app) as client:
        assert app.state.budget_pool is not pool
        assert app.state.budget_pool.max_size == 1
        assert client.get('/live').status_code == client.head('/live').status_code == 200
        assert used(pool, identity, 'service_invocations') == 0
        ready = client.get('/ready')
        missing = client.get('/missing')
        assert ready.status_code == 200 and missing.status_code == 404
        assert used(pool, identity, 'service_invocations') == 2
        assert used(pool, identity, 'api_response_bytes') == len(ready.content) + len(missing.content)
        assert used(pool, identity, 'google_egress_bytes') == len(ready.content) + len(missing.content) + 2 * 8192
    assert app.state.budget_pool.closed


def test_cors_preflight_is_charged_as_a_real_request(ledger_api):
    pool, settings, identity = ledger_api
    with TestClient(create_app(settings, pool, markers=FakeMarkers())) as client:
        response = client.options('/live', headers={'Origin': settings.allowed_origins[0],
                                                    'Access-Control-Request-Method': 'GET'})
    assert response.status_code == 200
    assert used(pool, identity, 'service_invocations') == 1
    assert used(pool, identity, 'api_response_bytes') == len(response.content)


def test_real_response_budget_failure_does_not_emit_requested_body(ledger_api):
    pool, settings, identity = ledger_api
    with pool.connection() as conn:
        conn.execute("UPDATE research.budget_metrics SET ceiling=0 WHERE window_id=%s AND metric='api_response_bytes'", (identity,))
    app = create_app(settings, pool, markers=FakeMarkers())
    @app.get('/private-fixture')
    def private():
        return {'secret': 'must never reach the client'}
    with TestClient(app) as client:
        response = client.get('/private-fixture')
        assert response.status_code == 503
        assert b'must never' not in response.content
    assert used(pool, identity, 'service_invocations') == 1
    assert used(pool, identity, 'api_response_bytes') == 0
    assert used(pool, identity, 'google_egress_bytes') == 0


def test_real_invocation_failure_prevents_endpoint_side_effect(ledger_api):
    pool, settings, identity = ledger_api
    with pool.connection() as conn:
        conn.execute("UPDATE research.budget_metrics SET ceiling=0 WHERE window_id=%s AND metric='service_invocations'", (identity,))
    app = create_app(settings, pool, markers=FakeMarkers())
    called = []
    @app.get('/effect')
    def effect():
        called.append(True)
        return {'ok': True}
    with TestClient(app) as client:
        assert client.get('/effect').status_code == 503
        assert client.get('/live').status_code == 200
    assert called == []


def test_independent_budget_pool_works_while_only_main_connection_is_held(ledger_api):
    pool, settings, identity = ledger_api
    app = create_app(settings, pool, markers=FakeMarkers())
    @app.get('/held-connection')
    def held():
        with pool.connection() as conn:
            conn.execute('SELECT 1')
            app.state.budget.charge(r2_class_b=1)
        return {'ok': True}
    with TestClient(app) as client:
        assert client.get('/held-connection').json() == {'ok': True}
    assert used(pool, identity, 'r2_class_b') == 1


def test_budget_connection_preserves_existing_role_options_in_the_same_dsn(ledger_api):
    pool, settings, _ = ledger_api
    restricted = replace(settings, database_url=make_conninfo(settings.database_url, options='-c role=research_api'))
    app = create_app(restricted, pool, markers=FakeMarkers())
    with TestClient(app) as client:
        assert client.get('/ready').status_code == 200
        with app.state.budget_pool.connection() as connection:
            assert connection.execute('SELECT current_user AS actor').fetchone()['actor'] == 'research_api'
            assert connection.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '5s'


def test_insufficient_search_allowance_rolls_back_all_acceptance_rows(ledger_api):
    pool, settings, identity = ledger_api
    with pool.connection() as conn:
        conn.execute("UPDATE research.budget_metrics SET ceiling=2 WHERE window_id=%s AND metric='search_attempts'", (identity,))
    markers = FakeMarkers()
    with patch('service.cloud.wake') as wake, TestClient(create_app(settings, pool, markers)) as client:
        response = client.post('/v1/queries', json={'publication_id': identity, 'query': {'team_size': {'min': 2, 'max': 2}}})
        assert response.status_code == 503
        wake.assert_not_called()
    with pool.connection() as conn:
        for table in ('query_runs', 'query_subscriptions', 'dispatch_outbox', 'capacity_reservations', 'budget_search_receipts'):
            assert conn.execute('SELECT count(*) AS n FROM research.' + table).fetchone()['n'] == 0
        assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 0
    assert used(pool, identity, 'search_attempts') == 0
    assert used(pool, identity, 'service_invocations') == 1
    assert len(markers.list()) == 1


def test_invalid_launch_bound_rejects_without_committing_search_work(ledger_api):
    pool, settings, identity = ledger_api
    with patch('service.cloud.wake') as wake, TestClient(create_app(replace(settings, max_launch_attempts=4), pool, FakeMarkers())) as client:
        response = client.post('/v1/queries', json={'publication_id': identity, 'query': {'team_size': {'min': 2, 'max': 2}}})
        assert response.status_code == 503
        wake.assert_not_called()
    with pool.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research.query_runs').fetchone()['n'] == 0
    assert used(pool, identity, 'search_attempts') == 0


def test_acceptance_commits_one_search_receipt_and_wakeup_budget_before_cloud_call(ledger_api):
    pool, settings, identity = ledger_api
    markers = FakeMarkers()
    calls = []
    def wake(config, run_id, generation, *, charge):
        charge()
        with psycopg.connect(settings.database_url, row_factory=dict_row) as observer:
            receipt = observer.execute('SELECT attempts FROM research.budget_search_receipts WHERE run_id=%s AND generation=%s', (run_id, generation)).fetchone()
            assert receipt['attempts'] == 3
        calls.append((run_id, generation))
    with patch('service.cloud.wake', side_effect=wake), TestClient(create_app(settings, pool, markers)) as client:
        body = {'publication_id': identity, 'query': {'team_size': {'min': 2, 'max': 2}}}
        first = client.post('/v1/queries', json=body)
        again = client.post('/v1/queries', json=body)
        assert first.status_code == again.status_code == 202
        assert first.json()['query_id'] == again.json()['query_id']
    assert len(calls) == len(markers.list()) == 1
    assert used(pool, identity, 'search_attempts') == 3
    assert used(pool, identity, 'cloud_control_calls') == 1
    with pool.connection() as conn:
        assert conn.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == search_liability(3)


def test_result_transport_receives_budget_callback_and_failed_startup_closes_budget_pool(ledger_api, tmp_path):
    pool, settings, _ = ledger_api
    settings = replace(settings, deployment_mode='vm', result_storage_mode='objects', result_local_root=str(tmp_path))
    app = create_app(settings, pool)
    with TestClient(app):
        assert app.state.result_objects.charge.__self__ is app.state.budget
    broken = create_app(settings, pool)
    with patch('service.result_objects.ResultObjects.from_settings', side_effect=ValueError('bad credentials')):
        with pytest.raises(ValueError, match='bad credentials'):
            with TestClient(broken):
                pass
    assert broken.state.budget_pool.closed
