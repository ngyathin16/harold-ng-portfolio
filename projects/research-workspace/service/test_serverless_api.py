"""Serverless API behavior against the same isolated PostgreSQL fixture as VM tests."""
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from service.app import create_app, network_identity
from service.config import Settings
from service.test_service import service, submission
from service.admission import record_sample


def test_liveness_never_requires_database():
    with TestClient(create_app(Settings())) as client:
        assert client.get('/live').json() == {'status': 'ok'}
        assert client.get('/ready').status_code == 503
        assert client.get('/health').status_code == 503


def test_serverless_requires_verified_tls_and_conservative_network(monkeypatch):
    monkeypatch.setenv('RESEARCH_DEPLOYMENT_MODE', 'serverless')
    monkeypatch.setenv('RESEARCH_CLIENT_IP_MODE', 'shared')
    monkeypatch.setenv('RESEARCH_DATABASE_URL', 'postgresql://fixture@db.invalid/research?sslmode=require')
    with pytest.raises(ValueError, match='verify-full'):
        Settings.from_env()
    monkeypatch.setenv('RESEARCH_DATABASE_URL', 'postgresql://fixture@db.invalid/research?sslmode=verify-full')
    assert Settings.from_env().deployment_mode == 'serverless'


def test_forwarding_headers_and_peer_rotation_do_not_change_serverless_quota():
    from starlette.requests import Request
    settings = Settings(client_ip_mode='shared')
    identities = []
    for peer, forged in [('10.0.0.1', '1.2.3.4'), ('10.0.0.2', '9.8.7.6')]:
        request = Request({'type': 'http', 'client': (peer, 123),
            'headers': [(b'x-forwarded-for', forged.encode()), (b'cf-connecting-ip', forged.encode())]})
        identities.append(network_identity(request, settings))
    assert identities[0] == identities[1]


def test_health_does_not_write_visitor_limits(service):
    client, pool, settings, pub, rules = service
    with pool.connection() as conn:
        before = conn.execute('SELECT COALESCE(sum(hits),0) AS n FROM research.request_limits').fetchone()['n']
    for route in ('/live', '/ready', '/health'):
        assert client.get(route).status_code == 200
    with pool.connection() as conn:
        assert conn.execute('SELECT COALESCE(sum(hits),0) AS n FROM research.request_limits').fetchone()['n'] == before


def test_acceptance_commits_outbox_before_wakeup_and_cache_does_not_dispatch(service):
    _, pool, settings, pub, rules = service
    settings = replace(settings, deployment_mode='serverless', client_ip_mode='shared')
    with pool.connection() as conn:
        record_sample(conn, 100, 10**12)
    observed = []
    def lost_wakeup(settings, run_id, generation):
        with pool.connection() as conn:
            row = conn.execute('SELECT generation FROM research.dispatch_outbox WHERE run_id=%s', (run_id,)).fetchone()
            assert row['generation'] == generation
            assert conn.execute('SELECT 1 FROM research.query_subscriptions WHERE run_id=%s', (run_id,)).fetchone()
        observed.append(run_id)
        raise RuntimeError('simulated task service unavailable')
    with patch('service.cloud.wake', side_effect=lost_wakeup), TestClient(create_app(settings, pool)) as client:
        first = submission(client, pub)
        again = submission(client, pub)
        assert first['query_id'] == again['query_id']
        assert observed == [first['query_id']]
        with pool.connection() as conn:
            conn.execute("UPDATE research.query_runs SET state='complete',expires_at=now()+interval '1 day' WHERE id=%s", (first['query_id'],))
            conn.execute("UPDATE research.capacity_sample SET measured_at=now()-interval '1 day'")
        cached = submission(client, pub)
        assert cached['state'] == 'complete'
        assert observed == [first['query_id']]


def test_stale_capacity_rejects_without_leaving_accepted_work(service):
    _, pool, settings, pub, rules = service
    settings = replace(settings, deployment_mode='serverless', client_ip_mode='shared')
    with pool.connection() as conn:
        record_sample(conn, 0, 10**12)
        conn.execute("UPDATE research.capacity_sample SET measured_at=now()-interval '1 day'")
    with TestClient(create_app(settings, pool)) as client:
        response = client.post('/v1/queries', json={'publication_id': pub, 'query': {'team_size': {'min': 2, 'max': 2}}})
        assert response.status_code == 503
        assert 'stale' in response.json()['detail']
    with pool.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research.query_runs WHERE publication_id=%s', (pub,)).fetchone()['n'] == 0


def test_shared_quotas_hold_across_apps_hostnames_and_forged_headers(service):
    _, pool, settings, pub, rules = service
    settings = replace(settings, deployment_mode='serverless', client_ip_mode='shared', max_requests_network=1)
    with pool.connection() as conn:
        record_sample(conn, 0, 10**12)
    with patch('service.cloud.wake', return_value=False), TestClient(create_app(settings, pool)) as first, TestClient(create_app(settings, pool)) as second:
        body = {'publication_id': pub, 'query': {'team_size': {'min': 2, 'max': 2}}}
        assert first.post('/v1/queries', json=body, headers={'host': 'api.example.invalid', 'x-forwarded-for': '1.2.3.4'}).status_code == 202
        assert second.post('/v1/queries', json=body, headers={'host': 'direct.run.app', 'x-forwarded-for': '9.8.7.6'}).status_code == 429


def test_storage_reservations_are_global_and_pause_keeps_saved_work(service):
    _, pool, settings, pub, rules = service
    settings = replace(settings, deployment_mode='serverless', client_ip_mode='shared',
                       result_reservation_bytes=100, database_headroom_bytes=10)
    with pool.connection() as conn:
        record_sample(conn, 10, 120)
    with patch('service.cloud.wake', return_value=False), TestClient(create_app(settings, pool)) as client:
        accepted = submission(client, pub)
        response = client.post('/v1/queries', json={'publication_id': pub, 'query': {'team_size': {'min': 1, 'max': 1}}})
        assert response.status_code == 503
        with pool.connection() as conn:
            conn.execute('UPDATE research.dispatch_control SET paused=true')
        try:
            assert submission(client, pub)['query_id'] == accepted['query_id']
            response = client.post('/v1/queries', json={'publication_id': pub, 'query': {'team_size': {'min': 3, 'max': 3}}})
            assert response.status_code == 503
            assert 'paused' in response.json()['detail']
        finally:
            with pool.connection() as conn:
                conn.execute('UPDATE research.dispatch_control SET paused=false')


def test_delayed_capacity_measurement_cannot_undo_newer_accounting(service):
    from datetime import datetime, timedelta, timezone
    _, pool, _, _, _ = service
    latest = datetime.now(timezone.utc)
    with pool.connection() as conn:
        record_sample(conn, 500, 1000, latest)
    with pytest.raises(ValueError, match='monotonically'), pool.connection() as conn:
        record_sample(conn, 100, 1000, latest - timedelta(seconds=60))
    with pool.connection() as conn:
        record_sample(conn, 500, 1000, latest)
        assert conn.execute('SELECT used_bytes FROM research.capacity_sample').fetchone()['used_bytes'] == 500
