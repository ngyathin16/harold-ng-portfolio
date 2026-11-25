"""Real PostgreSQL checkpoints and HTTP reads through the immutable object store."""
from copy import deepcopy
from dataclasses import replace
import threading
from unittest.mock import patch
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from service.app import create_app
from service.result_objects import ResultObjects
from service.result_store import ResultReader, ResultWriter, ResultLeaseLost, snapshot, shared_lock
from service.test_service import service, submission
from service.worker import claim, execute
from solver import solve


@pytest.fixture
def compact(service, tmp_path):
    _, pool, settings, pub, rules = service
    settings = replace(settings, result_storage_mode='objects', result_local_root=str(tmp_path/'objects'),
                       result_scratch_root=str(tmp_path/'scratch'))
    with TestClient(create_app(settings, pool=pool)) as client:
        yield client, pool, settings, pub, rules


def test_completed_http_results_keep_all_values_sorts_and_saved_identity(compact):
    client, pool, settings, pub, _ = compact
    accepted = submission(client, pub)
    run = claim(pool, settings)
    expected = []
    solve(run['definition'], run['rules'], on_result=expected.append)
    assert execute(pool, settings, run) == 'complete'
    key = lambda value: value['configuration_key']
    ordered = {
        'active_traits': sorted(expected, key=lambda v: (-v['active_trait_count'], key(v))),
        'total_cost': sorted(expected, key=lambda v: (v['total_cost'] is None, v['total_cost'], key(v))),
        'champions': sorted(expected, key=lambda v: (v['names_sort'].encode(), key(v))),
    }
    for order, wanted in ordered.items():
        actual = []
        for page in (1, 2, 3):
            response = client.get(f"/v1/queries/{run['id']}/results", params=dict(sort=order, page_size=2, page=page))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body['complete'] and body['total'] == 3
            actual.extend(body['rows'])
        assert actual == wanted
    cached = submission(client, pub)
    assert cached['query_id'] == accepted['query_id'] and cached['state'] == 'complete'
    with pool.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research.query_results WHERE run_id=%s', (run['id'],)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM research.result_object_intents WHERE generation_id=%s',
                            (snapshot(conn, run['id'])['result_generation_id'],)).fetchone()['n'] == 4


def test_resource_limited_is_incomplete_and_preserves_discovery(compact):
    client, pool, settings, pub, _ = compact
    run_id = submission(client, pub)['query_id']
    run = claim(pool, settings)
    assert execute(pool, replace(settings, max_results=1), run) == 'resource_limited'
    for order in ('active_traits', 'total_cost', 'champions'):
        body = client.get(f'/v1/queries/{run_id}/results', params={'sort': order}).json()
        assert not body['complete'] and body['total'] == len(body['rows']) == 1
        assert body['state'] == 'resource_limited'


def test_partial_segment_tail_cancel_and_stale_worker_fence(compact):
    client, pool, settings, pub, rules = compact
    rules = deepcopy(rules)
    rules['units'] = [{**rules['units'][0], 'form_id': str(i), 'unit_id': str(i),
                       'champion_id': str(i), 'name': f'Unit {i:02}', 'cost': i % 5 + 1} for i in range(20)]
    with pool.connection() as conn:
        conn.execute('UPDATE research.rulesets SET rules=%s WHERE publication_id=%s', (Jsonb(rules), pub))
    accepted = submission(client, pub, team_size={'min': 4, 'max': 4})
    run = claim(pool, settings)
    payloads = []
    solve(run['definition'], run['rules'], on_result=payloads.append)
    assert len(payloads) == 4845
    heartbeat = type('Heartbeat', (), {'lost': threading.Event()})()
    writer = ResultWriter(pool, settings, run, heartbeat)
    try:
        for start in range(0, 4000, 1000):
            writer.append(payloads[start:start + 1000], {})
        writer.append(payloads[4000:4600], {})
        response = client.get(f"/v1/queries/{run['id']}/results", params={'page_size': 200, 'page': 5})
        assert response.json()['rows'] == payloads[800:1000]
        with pool.connection() as conn:
            state = snapshot(conn, run['id'])
            objects = ResultObjects.from_settings(settings)
            try:
                assert ResultReader(objects).page(conn, state, 'champions', 4050, 200) == payloads[4050:4250]
            finally:
                objects.close()
        cancelled = client.post(f"/v1/queries/{run['id']}/cancel", json={'subscription_id': accepted['subscription_id']},
                                headers={'X-Session-Token': accepted['session_token']})
        assert cancelled.status_code == 200
        with pytest.raises(ResultLeaseLost):
            writer.append(payloads[4600:4700], {})
        body = client.get(f"/v1/queries/{run['id']}/results", params={'page_size': 200, 'page': 23}).json()
        assert body['state'] == 'cancelled' and not body['complete']
        assert body['total'] == 4600 and body['rows'] == payloads[4400:4600]
        again = submission(client, pub, team_size={'min': 4, 'max': 4})
        assert again['query_id'] == run['id']
        empty = client.get(f"/v1/queries/{run['id']}/results").json()
        assert empty['total'] == 0 and empty['rows'] == []
    finally:
        writer.close()


def test_failed_complete_upload_never_publishes_complete(compact):
    client, pool, settings, pub, _ = compact
    run_id = submission(client, pub)['query_id']
    run = claim(pool, settings)
    original = ResultObjects.put
    def interrupted(self, key, source):
        if key.endswith('/total_cost.bin'):
            original(self, key, source)
            raise OSError('Upload acknowledgement lost')
        return original(self, key, source)
    with patch.object(ResultObjects, 'put', interrupted):
        assert execute(pool, settings, run) == 'failed'
    response = client.get(f'/v1/queries/{run_id}/results')
    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 3 and len(body['rows']) == 3 and not body['complete']
    with pool.connection() as conn:
        state = snapshot(conn, run_id)
        assert state['complete_orders'] is None
        intent = conn.execute("SELECT state FROM research.result_object_intents WHERE generation_id=%s AND key LIKE '%%/total_cost.bin'",
                              (state['result_generation_id'],)).fetchone()
        assert intent['state'] == 'planned'


def test_corrupt_result_returns_unavailable_and_api_role_cannot_publish(compact):
    client, pool, settings, pub, _ = compact
    run_id = submission(client, pub)['query_id']
    run = claim(pool, settings)
    assert execute(pool, settings, run) == 'complete'
    with ConnectionPool(settings.database_url, min_size=1, max_size=1,
                        kwargs={'row_factory': dict_row, 'options': '-c role=research_api'}) as api_pool:
        with TestClient(create_app(settings, api_pool)) as restricted:
            assert restricted.get(f'/v1/queries/{run_id}/results').status_code == 200
        with api_pool.connection() as conn:
            import psycopg
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute('DELETE FROM research.result_generations')
            conn.rollback()
    with patch.object(ResultObjects, 'read_range', return_value=b'x' * 75):
        response = client.get(f'/v1/queries/{run_id}/results')
        assert response.status_code == 503
        assert 'rows' not in response.json()


def test_bad_manifest_and_zero_count_mismatch_fail_before_object_io():
    objects = Mock()
    reader = ResultReader(objects)
    state = dict(result_generation_id='a' * 32, result_count=0, visible_count=3,
                 state='complete', complete_orders=None)
    with pytest.raises(ValueError, match='disagree'):
        reader.page(None, state, 'active_traits', 0, 50)
    state.update(result_count=3, complete_orders={name: {} for name in ('active_traits', 'total_cost', 'champions')})
    with pytest.raises(ValueError, match='manifest'):
        reader.page(None, state, 'active_traits', 0, 50)
    objects.get.assert_not_called()


def test_beyond_end_page_does_not_fetch_dictionary():
    objects = Mock()
    reader = ResultReader(objects)
    state = dict(result_generation_id='a' * 32, result_count=3, visible_count=3,
                 state='cancelled', complete_orders=None)
    assert reader.page(None, state, 'active_traits', 3, 50) == []
    objects.get.assert_not_called()
