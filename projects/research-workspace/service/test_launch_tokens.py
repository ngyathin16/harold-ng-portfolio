"""Crash recovery and conditional replay against PostgreSQL and a fake control plane."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Lock

from fastapi.testclient import TestClient
import httpx
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from service.cloud import CloudRun
from service.dispatch import dispatch_once, enqueue, reconcile, reserve, save_observation
from service.dispatcher import create_app
from service.test_dispatch import FakeMarkers, queued, row
from service.test_service import service, submission


JOB = 'projects/fixture/locations/asia-east1/jobs/research-search'


class GoogleFixture:
    """The provider behavior assumed by the protocol, not provider qualification."""
    def __init__(self):
        self.lock = Lock()
        self.job = {'name': JOB, 'uid': 'fixture-job-uid', 'generation': '1', 'etag': 'v1',
                    'reconciling': False, 'template': {'taskCount': 1, 'parallelism': 1,
                    'template': {'maxRetries': 0, 'timeout': '300s', 'serviceAccount': 'worker@fixture.iam.gserviceaccount.com',
                    'containers': [{'image': 'registry.example/search@sha256:' + '1' * 64,
                    'command': ['python', '-m', 'service.worker'], 'args': ['--bound'],
                    'resources': {'limits': {'cpu': '2', 'memory': '2Gi'}}, 'env': []}]}}}
        self.executions = {}
        self.patches = []
        self.failure = None
        self.hide_executions = False

    def __call__(self, request):
        with self.lock:
            if request.url.host == 'metadata.google.internal':
                return httpx.Response(200, json={'access_token': 'fixture'})
            path = request.url.path.removeprefix('/v2/')
            if request.method == 'GET' and path == JOB:
                return httpx.Response(200, json=copy.deepcopy(self.job))
            if request.method == 'GET' and '/executions/' in path:
                value = self.executions.get(path)
                return httpx.Response(200, json=copy.deepcopy(value)) if value and not self.hide_executions else httpx.Response(404)
            if request.method == 'PATCH' and path == JOB:
                body = json.loads(request.content)
                self.patches.append(body)
                failure, self.failure = self.failure, None
                if failure == 'before':
                    raise httpx.ConnectError('Connection lost before request', request=request)
                if body['etag'] != self.job['etag']:
                    return httpx.Response(409, json={'error': {'status': 'ABORTED'}})
                self.job.update(copy.deepcopy(body))
                generation = int(self.job['generation']) + 1
                self.job.update(generation=str(generation), etag='v' + str(generation))
                name = JOB + '/executions/research-search-' + body['startExecutionToken']
                assert name not in self.executions, 'One conditional mutation must create at most one execution'
                self.executions[name] = {'name': name, 'template': copy.deepcopy(body['template']['template'])}
                if failure == 'after':
                    raise httpx.ReadTimeout('Acknowledgement lost', request=request)
                return httpx.Response(200, json={'name': 'projects/fixture/locations/asia-east1/operations/op-' + str(generation)})
            raise AssertionError((request.method, path))

    def cloud(self, settings):
        return CloudRun(replace(settings, dispatch_job=JOB), httpx.Client(transport=httpx.MockTransport(self)))

    def complete(self):
        for value in self.executions.values():
            value['completionTime'] = '2026-11-25T00:00:00Z'


def reservation_row(pool, identity):
    with pool.connection() as conn:
        return conn.execute('SELECT * FROM research.execution_reservations WHERE id=%s', (identity,)).fetchone()


@pytest.mark.parametrize('persist_request', [False, True])
def test_crash_before_request_recovers_same_reservation(service, persist_request):
    pool, settings, run_id, _ = queued(service)
    provider = GoogleFixture()
    cloud = provider.cloud(settings)
    reservation = reserve(pool, settings, cloud.launch_protocol)
    if persist_request:
        with pool.connection() as conn:
            conn.execute('UPDATE research.execution_reservations SET launch_spec=%s WHERE id=%s',
                         (Jsonb(cloud.prepare(reservation)), reservation['id']))
    assert provider.executions == {}
    dispatch_once(pool, settings, cloud)
    assert len(provider.executions) == 1
    current = reservation_row(pool, reservation['id'])
    assert current['launch_confirmed_at'] and current['state'] == 'running'
    with pool.connection() as conn:
        assert conn.execute('SELECT launch_attempts FROM research.dispatch_outbox WHERE run_id=%s', (run_id,)).fetchone()['launch_attempts'] == 1


@pytest.mark.parametrize('failure', ['before', 'after'])
def test_uncertain_request_replay_creates_only_one_paid_execution(service, failure):
    pool, settings, _, _ = queued(service)
    provider = GoogleFixture()
    provider.failure = failure
    cloud = provider.cloud(settings)
    assert dispatch_once(pool, settings, cloud)['retry_pending']
    assert len(provider.patches) == 1, 'Transport must not automatically retry a mutation'
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: dispatch_once(pool, settings, cloud), range(4)))
    assert len(provider.executions) == 1
    assert not dispatch_once(pool, settings, cloud)['retry_pending']
    assert len({body['startExecutionToken'] for body in provider.patches}) == 1


def test_unconfirmed_update_blocks_next_token_and_requests_fast_wakeup(service):
    client, pool, settings, publication, _ = service
    markers = FakeMarkers()
    settings = replace(settings, deployment_mode='serverless', max_outstanding=2, dispatch_batch_size=2)
    first = submission(client, publication)
    second = submission(client, publication, include=['1'])
    with pool.connection() as conn:
        enqueue(conn, first['query_id'], settings, markers)
        enqueue(conn, second['query_id'], settings, markers)
    provider = GoogleFixture()
    provider.hide_executions = True
    cloud = provider.cloud(settings)
    app = create_app(settings, pool=pool, cloud=cloud, markers=markers)
    with TestClient(app) as wakeup:
        assert wakeup.post('/dispatch', json={}).status_code == 503
        assert len(provider.executions) == 1
        assert wakeup.post('/dispatch', json={}).status_code == 503
        assert len(provider.patches) == 1
        provider.hide_executions = False
        result = wakeup.post('/dispatch', json={})
        assert result.status_code == 200
        assert len(provider.executions) == result.json()['outstanding'] == 2


def test_delayed_old_conditional_request_cannot_launch_after_next_attempt(service):
    pool, settings, run_id, _ = queued(service)
    provider = GoogleFixture()
    cloud = provider.cloud(settings)
    dispatch_once(pool, settings, cloud)
    old_body = copy.deepcopy(provider.patches[0])
    provider.complete()
    reconcile(pool, settings, cloud)
    with pool.connection() as conn:
        conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=%s', (run_id,))
    dispatch_once(pool, settings, cloud)
    assert len(provider.executions) == 2
    response = cloud.request('PATCH', JOB, json=old_body)
    assert response.status_code == 409
    assert len(provider.executions) == 2


def test_three_terminal_attempts_exhaust_budget_without_fourth_execution(service):
    pool, settings, run_id, _ = queued(service)
    provider = GoogleFixture()
    cloud = provider.cloud(settings)
    for _ in range(5):
        dispatch_once(pool, settings, cloud)
        provider.complete()
        reconcile(pool, settings, cloud)
        with pool.connection() as conn:
            conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=%s', (run_id,))
    assert len(provider.executions) == settings.max_launch_attempts == 3
    assert row(pool, run_id)['state'] == 'failed'


def test_two_searches_never_exceed_two_outstanding_or_six_paid_attempts(service):
    client, pool, settings, publication, _ = service
    settings = replace(settings, deployment_mode='serverless', max_outstanding=2, dispatch_batch_size=2)
    run_ids = [submission(client, publication)['query_id'], submission(client, publication, include=['1'])['query_id']]
    with pool.connection() as conn:
        for run_id in run_ids:
            enqueue(conn, run_id, settings, FakeMarkers())
    provider = GoogleFixture()
    cloud = provider.cloud(settings)
    for _ in range(5):
        report = dispatch_once(pool, settings, cloud)
        assert report['outstanding'] <= 2
        assert sum('completionTime' not in item for item in provider.executions.values()) <= 2
        provider.complete()
        reconcile(pool, settings, cloud)
        with pool.connection() as conn:
            conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=ANY(%s)', (run_ids,))
            conn.execute("UPDATE research.execution_reservations SET created_at=now()-interval '2 minutes' WHERE run_id=ANY(%s)", (run_ids,))
    assert len(provider.executions) == 6
    assert all(row(pool, identity)['state'] == 'failed' for identity in run_ids)


def test_foreign_job_mutation_does_not_rebase_or_release_uncertain_intent(service):
    pool, settings, _, _ = queued(service)
    provider = GoogleFixture()
    provider.failure = 'before'
    cloud = provider.cloud(settings)
    dispatch_once(pool, settings, cloud)
    provider.job.update(generation='2', etag='foreign')
    assert dispatch_once(pool, settings, cloud)['retry_pending']
    assert len(provider.patches) == 1 and not provider.executions
    assert reserve(pool, settings, cloud.launch_protocol) is None


def test_status_only_etag_change_can_recover_without_new_paid_attempt(service):
    pool, settings, _, _ = queued(service)
    provider = GoogleFixture()
    provider.failure = 'before'
    cloud = provider.cloud(settings)
    dispatch_once(pool, settings, cloud)
    provider.job['etag'] = 'new-status-only-etag'
    dispatch_once(pool, settings, cloud)
    assert len(provider.executions) == 1
    assert provider.patches[-1]['etag'] == 'new-status-only-etag'


def test_stale_observation_cannot_regress_confirmed_execution(service):
    pool, settings, _, _ = queued(service)
    provider = GoogleFixture()
    cloud = provider.cloud(settings)
    reservation = reserve(pool, settings, cloud.launch_protocol)
    dispatch_once(pool, settings, cloud)
    save_observation(pool, settings, reservation, {'state': 'uncertain'})
    assert reservation_row(pool, reservation['id'])['state'] == 'running'


def test_execution_name_is_insufficient_without_exact_query_binding(service):
    pool, settings, _, _ = queued(service)
    provider = GoogleFixture()
    cloud = provider.cloud(settings)
    reservation = reserve(pool, settings, cloud.launch_protocol)
    dispatch_once(pool, settings, cloud)
    value = next(iter(provider.executions.values()))
    value['template']['containers'][0]['env'][0]['value'] = 'wrong-query'
    with pytest.raises(ValueError, match='reserved search'):
        cloud.observe(reservation_row(pool, reservation['id']))


def test_worker_cannot_replace_durable_launch_request(service):
    pool, settings, _, _ = queued(service)
    reservation = reserve(pool, settings, 'token-v1')
    with pytest.raises(InsufficientPrivilege), pool.connection() as conn:
        conn.execute('SET LOCAL ROLE research_worker')
        conn.execute("UPDATE research.execution_reservations SET launch_spec='{}' WHERE id=%s", (reservation['id'],))


@pytest.mark.parametrize('change', ['bytes', 'cpu', 'tasks'])
def test_unqualified_or_oversized_template_never_sends_a_launch(service, change):
    pool, settings, _, _ = queued(service)
    provider = GoogleFixture()
    if change == 'bytes':
        provider.job['annotations'] = {'large': '\u6f22' * 21000}
    elif change == 'cpu':
        provider.job['template']['template']['containers'][0]['resources']['limits']['cpu'] = '4'
    else:
        provider.job['template']['taskCount'] = 2
    cloud = provider.cloud(settings)
    assert dispatch_once(pool, settings, cloud)['retry_pending']
    assert not provider.patches and not provider.executions


def test_allowlisted_job_name_must_fit_execution_suffix():
    from service.config import Settings
    with pytest.raises(ValueError, match='insufficient room'):
        CloudRun(Settings(dispatch_job='projects/fixture/locations/asia-east1/jobs/' + 'x' * 31))
