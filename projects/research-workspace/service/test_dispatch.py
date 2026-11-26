"""Failure injection against the real PostgreSQL outbox and fake cloud boundary."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time
import uuid
from unittest.mock import patch

import httpx
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from service.cloud import CloudRun, wake
from service.config import Settings
from service.dispatch import LaunchRejected, dispatch_once, enqueue, reconcile, reserve
from service.markers import Marker
from service.test_service import service, submission
from service.worker import claim, claim_bound, execute


class FakeCloud:
    def __init__(self, error=None):
        self.error = error
        self.launches = []
        self.states = {}

    def launch(self, reservation):
        self.launches.append(dict(reservation))
        if self.error:
            raise self.error
        self.states[reservation['id']] = {'state': 'running', 'execution_name': 'fixture'}
        return 'operation-fixture'

    def observe(self, reservation):
        return self.states.get(reservation['id'], {'state': 'uncertain'})


class FakeMarkers:
    def __init__(self, error=None, store_before_error=False):
        self.error = error
        self.store_before_error = store_before_error
        self.objects = {}

    def create(self, run_id, generation):
        if self.error and not self.store_before_error:
            raise self.error
        name = f'{run_id}/{generation}/{uuid.uuid4().hex}'
        self.objects[name] = Marker(name, '1', run_id, generation)
        if self.error:
            raise self.error

    def list(self):
        return list(self.objects.values())

    def delete(self, marker):
        current = self.objects.get(marker.name)
        if current is not None and current.object_generation == marker.object_generation:
            del self.objects[marker.name]


def queued(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    settings = replace(settings, deployment_mode='serverless', max_outstanding=1, dispatch_batch_size=1)
    with pool.connection() as conn:
        generation = enqueue(conn, run_id, settings, FakeMarkers())
    return pool, settings, run_id, generation


def row(pool, run_id):
    with pool.connection() as conn:
        return conn.execute('SELECT * FROM research.query_runs WHERE id=%s', (run_id,)).fetchone()


def test_outbox_rolls_back_with_acceptance(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    with pytest.raises(RuntimeError), pool.connection() as conn:
        enqueue(conn, run_id, settings)
        raise RuntimeError('API terminated before commit')
    with pool.connection() as conn:
        assert conn.execute('SELECT * FROM research.dispatch_outbox WHERE run_id=%s', (run_id,)).fetchone() is None
    assert row(pool, run_id)['dispatch_generation'] == 0


def test_api_loss_after_commit_is_recovered_without_visitor(service):
    pool, settings, run_id, generation = queued(service)
    cloud = FakeCloud()
    assert dispatch_once(pool, settings, cloud)['launches'] == 1
    assert cloud.launches[0]['run_id'] == run_id
    assert cloud.launches[0]['generation'] == generation
    assert dispatch_once(pool, settings, cloud)['launches'] == 0
    assert len(cloud.launches) == 1


def test_uncertain_launch_and_expired_reservation_keep_capacity(service):
    pool, settings, run_id, _ = queued(service)
    cloud = FakeCloud(TimeoutError())
    dispatch_once(pool, settings, cloud)
    with pool.connection() as conn:
        conn.execute("UPDATE research.execution_reservations SET expires_at=now()-interval '1 day' WHERE run_id=%s", (run_id,))
    for _ in range(3):
        dispatch_once(pool, settings, cloud)
    assert len(cloud.launches) == 1
    assert reserve(pool, settings) is None
    assert row(pool, run_id)['state'] == 'queued'
    cloud.error = None
    cloud.states[cloud.launches[0]['id']] = {'state': 'terminal'}
    reconcile(pool, settings, cloud)
    with pool.connection() as conn:
        conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=%s', (run_id,))
    assert dispatch_once(pool, settings, cloud)['launches'] == 1


def test_rejected_launch_uses_finite_budget(service):
    pool, settings, run_id, _ = queued(service)
    cloud = FakeCloud(LaunchRejected())
    for _ in range(settings.max_launch_attempts + 2):
        dispatch_once(pool, settings, cloud)
        with pool.connection() as conn:
            conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=%s', (run_id,))
    assert len(cloud.launches) == settings.max_launch_attempts
    assert row(pool, run_id)['state'] == 'failed'


def test_concurrent_dispatchers_reserve_once(service):
    pool, settings, _, _ = queued(service)
    with ThreadPoolExecutor(max_workers=4) as workers:
        reservations = list(workers.map(lambda _: reserve(pool, settings), range(8)))
    assert sum(value is not None for value in reservations) == 1


def test_launch_rate_limit_retains_pending_work(service):
    pool, settings, run_id, _ = queued(service)
    settings = replace(settings, max_launches_per_minute=1)
    cloud = FakeCloud(LaunchRejected())
    dispatch_once(pool, settings, cloud)
    with pool.connection() as conn:
        conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=%s', (run_id,))
    dispatch_once(pool, settings, cloud)
    assert len(cloud.launches) == 1
    assert row(pool, run_id)['state'] == 'queued'
    with pool.connection() as conn:
        assert conn.execute('SELECT state FROM research.dispatch_outbox WHERE run_id=%s', (run_id,)).fetchone()['state'] == 'pending'


def test_queue_age_tracks_current_generation_not_saved_identity(service):
    pool, settings, run_id, _ = queued(service)
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET created_at=now()-interval '30 days' WHERE id=%s", (run_id,))
    report = dispatch_once(pool, settings, FakeCloud())
    assert report['queue_age_seconds'] < 60


def test_bound_worker_cannot_claim_wrong_generation_or_duplicate(service):
    pool, settings, run_id, generation = queued(service)
    reservation = reserve(pool, settings)
    assert claim_bound(pool, settings, 'different-run', generation, reservation['id']) is None
    assert claim_bound(pool, settings, run_id, generation + 1, reservation['id']) is None
    run = claim_bound(pool, settings, run_id, generation, reservation['id'])
    assert run['id'] == run_id
    assert claim_bound(pool, settings, run_id, generation, reservation['id']) is None
    execute(pool, settings, run)
    assert row(pool, run_id)['state'] == 'complete'
    assert row(pool, run_id)['result_count'] == 3
    cloud = FakeCloud()
    cloud.states[reservation['id']] = {'state': 'terminal'}
    reconcile(pool, settings, cloud)
    assert reserve(pool, settings) is None
    assert row(pool, run_id)['attempts'] == 1


def test_bound_worker_loss_retries_only_after_cloud_terminal(service):
    pool, settings, run_id, generation = queued(service)
    reservation = reserve(pool, settings)
    old = claim_bound(pool, settings, run_id, generation, reservation['id'])
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET lease_until=now()-interval '1 minute' WHERE id=%s", (run_id,))
    cloud = FakeCloud()
    reconcile(pool, settings, cloud)
    assert reserve(pool, settings) is None
    cloud.states[reservation['id']] = {'state': 'terminal'}
    reconcile(pool, settings, cloud)
    with pool.connection() as conn:
        conn.execute('UPDATE research.dispatch_outbox SET eligible_at=now() WHERE run_id=%s', (run_id,))
    next_reservation = reserve(pool, settings)
    new = claim_bound(pool, settings, run_id, generation, next_reservation['id'])
    with patch('service.worker.solve', side_effect=AssertionError('Fenced worker must not enumerate')):
        assert execute(pool, settings, old) == 'lease_lost'
    execute(pool, settings, new)
    assert row(pool, run_id)['state'] == 'complete'
    assert row(pool, run_id)['attempts'] == 2


def test_dispatcher_and_worker_use_restricted_database_roles(service):
    pool, settings, run_id, generation = queued(service)
    with ConnectionPool(settings.database_url, min_size=0, max_size=2,
                        kwargs={'row_factory': dict_row, 'options': '-c role=research_dispatcher'}) as dispatcher:
        reservation = reserve(dispatcher, settings)
    with ConnectionPool(settings.database_url, min_size=0, max_size=2,
                        kwargs={'row_factory': dict_row, 'options': '-c role=research_worker'}) as worker:
        run = claim_bound(worker, settings, run_id, generation, reservation['id'])
        assert execute(worker, settings, run) == 'complete'
    assert row(pool, run_id)['result_count'] == 3


def test_lease_renews_during_compilation_without_solver_callbacks(service):
    pool, settings, run_id, generation = queued(service)
    reservation = reserve(pool, settings)
    run = claim_bound(pool, settings, run_id, generation, reservation['id'])
    def compile_then_complete(*args, **kwargs):
        before = row(pool, run_id)['lease_until']
        time.sleep(8)
        assert (row(pool, run_id)['lease_until'] - before).total_seconds() >= 4
        return {'state': 'complete', 'complete': True, 'configuration_count': 0}
    with patch('service.worker.solve', side_effect=compile_then_complete):
        assert execute(pool, settings, run) == 'complete'
    assert row(pool, run_id)['state'] == 'complete'


def test_serverless_worker_refuses_generic_queue_claim():
    with pytest.raises(ValueError, match='bind'):
        claim(None, Settings(deployment_mode='serverless'))


def test_cloud_launch_rejects_unrecorded_request_and_untrusted_job():
    settings = Settings(dispatch_job='projects/example-project/locations/asia-east1/jobs/search')
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == 'metadata.google.internal':
            return httpx.Response(200, json={'access_token': 'test-token'})
        return httpx.Response(503)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    cloud = CloudRun(settings, client)
    with pytest.raises(ValueError, match='durable'):
        cloud.launch({'run_id': 'query', 'generation': 1, 'id': 'reservation'})
    assert calls == []
    with pytest.raises(ValueError):
        CloudRun(Settings(dispatch_job='https://untrusted.example/run'))


def test_empty_cloud_listing_never_proves_absence():
    def handler(request):
        return httpx.Response(200, json={'access_token': 'test'} if request.url.host == 'metadata.google.internal' else {'executions': []})
    cloud = CloudRun(Settings(dispatch_job='projects/example/locations/asia-east1/jobs/search'),
                     httpx.Client(transport=httpx.MockTransport(handler)))
    assert cloud.observe({'id': 'reservation'}) == {'state': 'uncertain'}


def test_wakeup_failure_does_not_lose_accepted_identity():
    with patch('service.cloud.httpx.Client', side_effect=TimeoutError):
        assert wake(Settings(task_queue='projects/example/locations/asia-east1/queues/search',
                             dispatcher_url='https://dispatcher.run.app',
                             task_service_account='tasks@example.iam.gserviceaccount.com'), 'accepted-query', 1) is False
