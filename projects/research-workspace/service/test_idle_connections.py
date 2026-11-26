"""Idle server disconnects are replaced before business or budget transactions."""
from dataclasses import replace

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
import pytest

from service.app import create_app
from service.dispatcher import create_app as create_dispatcher
from service.test_budget_http import ledger_api, used
from service.test_dispatch import FakeCloud, FakeMarkers


def terminate_idle_connection(pool, observer):
    with pool.connection() as connection:
        pid = connection.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    with observer.connection() as admin:
        assert admin.execute('SELECT pg_terminate_backend(%s,5000) AS stopped', (pid,)).fetchone()['stopped']
        assert admin.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()['n'] == 0
    # The cached client has not tried to use its socket since the server closed it.
    assert not connection.closed
    return pid


@pytest.mark.parametrize('target', ['pool', 'budget_pool'])
def test_ready_replaces_dead_idle_connection_and_debits_once(ledger_api, target):
    observer, settings, identity = ledger_api
    settings = replace(settings, database_url=make_conninfo(settings.database_url, options='-c role=research_api'))
    app = create_app(settings, markers=FakeMarkers())
    with TestClient(app) as client:
        first = client.get('/ready')
        assert first.status_code == 200 and first.json() == {'status': 'ok', 'search': 'available'}
        dead_pid = terminate_idle_connection(getattr(app.state, target), observer)
        recovered = client.get('/ready')
        assert recovered.status_code == 200 and recovered.json() == first.json()
        with getattr(app.state, target).connection() as connection:
            assert connection.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'] != dead_pid
        assert used(observer, identity, 'service_invocations') == 2
        assert used(observer, identity, 'api_response_bytes') == len(first.content) + len(recovered.content)
        assert used(observer, identity, 'google_egress_bytes') == len(first.content) + len(recovered.content) + 2 * 8192


@pytest.mark.parametrize('target', ['pool', 'budget_pool'])
def test_dispatch_replaces_dead_idle_connection_without_repeating_work(ledger_api, monkeypatch, target):
    observer, settings, identity = ledger_api
    settings = replace(settings, database_url=make_conninfo(settings.database_url, options='-c role=research_dispatcher'))
    monkeypatch.setenv('K_SERVICE', 'local-dispatcher-fixture')
    markers, cloud = FakeMarkers(), FakeCloud()
    app = create_dispatcher(settings, cloud=cloud, markers=markers)
    with TestClient(app) as client:
        markers.create('absent-fixture-run', 1)
        first = client.post('/dispatch', json={})
        assert first.status_code == 200 and first.json()['launches'] == 0
        dead_pid = terminate_idle_connection(getattr(app.state, target), observer)
        markers.create('absent-fixture-run', 1)
        recovered = client.post('/dispatch', json={})
        assert recovered.status_code == 200 and recovered.json()['launches'] == 0
        with getattr(app.state, target).connection() as connection:
            assert connection.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'] != dead_pid
        assert used(observer, identity, 'service_invocations') == 2
        assert used(observer, identity, 'gcs_class_a') == 34
        assert used(observer, identity, 'google_egress_bytes') == 34 * 8192
        assert not cloud.launches and not markers.list()
        assert client.post('/dispatch', json={}).json() == {'launches': 0, 'idle': True}
        assert used(observer, identity, 'service_invocations') == 2
