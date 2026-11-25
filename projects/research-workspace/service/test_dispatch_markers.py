"""Durable wake hints preserve accepted work without polling an idle database."""
from dataclasses import replace
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from service.dispatch import dispatch_once, enqueue, reconcile
from service.dispatcher import create_app
from service.config import Settings
from service.markers import GCSMarkers, MarkerUnavailable, cleanup, repair
from service.test_dispatch import FakeCloud, FakeMarkers, row
from service.test_service import service, submission


def accepted_with_markers(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    settings = replace(settings, deployment_mode='serverless', max_outstanding=1, dispatch_batch_size=1)
    markers = FakeMarkers()
    with pool.connection() as conn:
        generation = enqueue(conn, run_id, settings, markers)
    return pool, settings, run_id, generation, markers


def test_empty_marker_listing_never_connects_to_database():
    pool = Mock()
    pool.connection.side_effect = AssertionError('Idle dispatch must not wake PostgreSQL')
    cloud = FakeCloud()
    with TestClient(create_app(Settings(), pool, cloud, markers=FakeMarkers())) as client:
        response = client.post('/dispatch', json={})
        assert response.status_code == 200
        assert client.get('/health/live').status_code == 200
    pool.connection.assert_not_called()
    assert cloud.launches == []


def test_marker_listing_failure_is_not_reported_as_empty():
    pool = Mock()
    markers = FakeMarkers()
    with patch.object(markers, 'list', side_effect=TimeoutError), TestClient(create_app(Settings(), pool, FakeCloud(), markers=markers)) as client:
        assert client.post('/dispatch', json={}).status_code == 503
    pool.connection.assert_not_called()


def test_uncommitted_marker_is_retained_until_acceptance_finishes(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    settings = replace(settings, deployment_mode='serverless')
    markers = FakeMarkers()
    with pool.connection() as conn:
        generation = enqueue(conn, run_id, settings, markers)
        listed = markers.list()
        assert len(listed) == 1
        assert row(pool, run_id)['dispatch_generation'] == 0
        cleanup(pool, markers, listed)
        assert markers.list() == listed
    assert row(pool, run_id)['dispatch_generation'] == generation
    cleanup(pool, markers, listed)
    assert markers.list() == listed


def test_api_loss_after_marker_write_before_commit_leaves_removable_orphan(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    settings = replace(settings, deployment_mode='serverless')
    markers = FakeMarkers()
    with pytest.raises(RuntimeError), pool.connection() as conn:
        enqueue(conn, run_id, settings, markers)
        raise RuntimeError('API terminated before acceptance commit')
    assert len(markers.list()) == 1
    assert row(pool, run_id)['dispatch_generation'] == 0
    cleanup(pool, markers, markers.list())
    assert markers.list() == []


def test_api_loss_after_commit_before_task_is_recovered_from_marker(service):
    pool, settings, run_id, generation, markers = accepted_with_markers(service)
    cloud = FakeCloud()
    with TestClient(create_app(settings, pool, cloud, markers=markers)) as client:
        assert client.post('/dispatch', json={}).status_code == 200
        assert client.post('/dispatch', json={}).status_code == 200
    assert len(cloud.launches) == 1
    assert cloud.launches[0]['run_id'] == run_id
    assert cloud.launches[0]['generation'] == generation
    assert len(markers.list()) == 1


def test_rolled_back_generation_reuse_cannot_delete_new_marker(service):
    client, pool, settings, publication, _ = service
    run_id = submission(client, publication)['query_id']
    settings = replace(settings, deployment_mode='serverless')
    markers = FakeMarkers()
    with pytest.raises(RuntimeError), pool.connection() as conn:
        old_generation = enqueue(conn, run_id, settings, markers)
        raise RuntimeError('Acceptance rolled back')
    orphan = markers.list()[0]
    with pool.connection() as conn:
        generation = enqueue(conn, run_id, settings, markers)
        cleanup(pool, markers, [orphan])
        assert len(markers.list()) == 2
    assert generation == old_generation
    new_marker = next(marker for marker in markers.list() if marker.name != orphan.name)
    cleanup(pool, markers, [orphan])
    assert len(markers.list()) == 2
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET state='cancelled' WHERE id=%s", (run_id,))
    cleanup(pool, markers, [orphan])
    assert markers.list() == [new_marker]


def test_terminal_query_keeps_marker_until_uncertain_cloud_launch_is_terminal(service):
    pool, settings, run_id, _, markers = accepted_with_markers(service)
    cloud = FakeCloud(TimeoutError())
    dispatch_once(pool, settings, cloud)
    reservation = cloud.launches[0]
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET state='cancelled' WHERE id=%s", (run_id,))
        conn.execute("UPDATE research.execution_reservations SET expires_at=now()-interval '1 day' WHERE id=%s", (reservation['id'],))
    cleanup(pool, markers, markers.list())
    assert len(markers.list()) == 1
    reconcile(pool, settings, cloud)
    cleanup(pool, markers, markers.list())
    assert len(markers.list()) == 1
    cloud.states[reservation['id']] = {'state': 'terminal'}
    reconcile(pool, settings, cloud)
    cleanup(pool, markers, markers.list())
    assert markers.list() == []


def test_old_generation_keeps_own_marker_while_new_generation_is_queued(service):
    pool, settings, run_id, generation, markers = accepted_with_markers(service)
    old_marker = markers.list()[0]
    cloud = FakeCloud(TimeoutError())
    dispatch_once(pool, settings, cloud)
    with pool.connection() as conn:
        new_generation = enqueue(conn, run_id, settings, markers)
    assert new_generation == generation + 1
    cleanup(pool, markers, markers.list())
    assert len(markers.list()) == 2
    cloud.states[cloud.launches[0]['id']] = {'state': 'terminal'}
    reconcile(pool, settings, cloud)
    cleanup(pool, markers, markers.list())
    assert len(markers.list()) == 1
    assert markers.list()[0].name != old_marker.name
    assert markers.list()[0].generation == new_generation


def test_repair_covers_current_queue_and_outstanding_older_generation(service):
    pool, settings, run_id, generation, markers = accepted_with_markers(service)
    dispatch_once(pool, settings, FakeCloud(TimeoutError()))
    with pool.connection() as conn:
        new_generation = enqueue(conn, run_id, settings, markers)
        conn.execute('UPDATE research.dispatch_control SET paused=true')
    repaired = FakeMarkers()
    try:
        with pool.connection() as conn:
            repair(conn, repaired)
        assert {(marker.run_id, marker.generation) for marker in repaired.list()} >= {(run_id, generation), (run_id, new_generation)}
    finally:
        with pool.connection() as conn:
            conn.execute('UPDATE research.dispatch_control SET paused=false')


def test_repair_requires_dispatch_pause(service):
    _, pool, _, _, _ = service
    with pytest.raises(ValueError, match='[Pp]aus'), pool.connection() as conn:
        repair(conn, FakeMarkers())


def test_marker_adapter_uses_unique_names_and_conditional_delete_without_metadata():
    requests = []
    objects = {}

    def handler(request):
        requests.append(request)
        assert request.url.host == 'storage.googleapis.com'
        assert request.headers['authorization'] == 'Bearer operator-token'
        if request.method == 'POST':
            assert request.url.params['ifGenerationMatch'] == '0'
            name = request.url.params['name']
            objects[name] = {'name': name, 'generation': str(len(objects) + 1)}
            return httpx.Response(200, json=objects[name])
        if request.method == 'GET':
            assert request.url.params['prefix'] == 'test-environment/'
            return httpx.Response(200, json={'items': list(objects.values())})
        if request.method == 'DELETE':
            assert request.url.params['ifGenerationMatch'] == '1'
            return httpx.Response(412)
        raise AssertionError('Unexpected marker request')

    markers = GCSMarkers(Settings(dispatch_marker_bucket='fixture-markers', dispatch_marker_prefix='test-environment'),
                         httpx.Client(transport=httpx.MockTransport(handler)), access_token='operator-token')
    markers.create('query/with spaces', 1)
    markers.create('query/with spaces', 1)
    listed = markers.list()
    assert len(listed) == 2
    assert listed[0].name != listed[1].name
    assert {(marker.run_id, marker.generation) for marker in listed} == {('query/with spaces', 1)}
    markers.delete(listed[0])
    assert len(requests) == 4
    markers.close()


def test_marker_creation_requires_confirmation_of_the_exact_object():
    def handler(request):
        name = request.url.params['name']
        other = name.rsplit('/', 1)[0] + '/' + '0' * 32
        return httpx.Response(200, json={'name': other, 'generation': '1'})

    markers = GCSMarkers(Settings(dispatch_marker_bucket='fixture-markers'),
                         httpx.Client(transport=httpx.MockTransport(handler)), access_token='operator-token')
    with pytest.raises(ValueError, match='confirmed'):
        markers.create('query', 1)
    markers.close()


def test_marker_listing_error_cannot_look_like_no_work():
    markers = GCSMarkers(Settings(dispatch_marker_bucket='fixture-markers'),
                         httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))),
                         access_token='operator-token')
    with pytest.raises(httpx.HTTPStatusError):
        markers.list()
    markers.close()


def test_empty_partial_marker_page_is_not_idle():
    markers = GCSMarkers(Settings(dispatch_marker_bucket='fixture-markers'),
                         httpx.Client(transport=httpx.MockTransport(
                             lambda request: httpx.Response(200, json={'nextPageToken': 'more'}))),
                         access_token='operator-token')
    with pytest.raises(MarkerUnavailable, match='incomplete'):
        markers.list()
    markers.close()


def test_orphan_page_still_dispatches_work_and_bounds_cleanup(service):
    pool, settings, run_id, _, markers = accepted_with_markers(service)
    orphan_page = FakeMarkers()
    for index in range(40):
        orphan_page.create('rolled-back-' + str(index), 1)
    cloud = FakeCloud()
    with patch.object(markers, 'list', return_value=orphan_page.list()), \
            patch.object(markers, 'delete', side_effect=orphan_page.delete), \
            TestClient(create_app(settings, pool, cloud, markers=markers)) as client:
        assert client.post('/dispatch', json={}).status_code == 200
    assert cloud.launches[0]['run_id'] == run_id
    assert len(orphan_page.list()) == 24
