"""Real SQL blocking and process termination keep dispatcher work bounded."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from service.app import create_app
from service.dispatcher import create_app as create_dispatcher
from service.test_budget_http import ledger_api
from service.test_dispatch import FakeCloud, FakeMarkers


def test_blocked_admission_returns_bounded_failure_without_spending_launch(ledger_api, monkeypatch):
    observer, settings, identity = ledger_api
    markers, cloud = FakeMarkers(), FakeCloud()
    with patch('service.cloud.wake'), TestClient(create_app(settings, observer, markers)) as api:
        response = api.post('/v1/queries', json={'publication_id': identity, 'query': {'team_size': {'min': 2, 'max': 2}}})
    assert response.status_code == 202
    run_id = response.json()['query_id']
    restricted = replace(settings, database_url=make_conninfo(settings.database_url, options='-c role=research_dispatcher'))
    monkeypatch.setenv('K_SERVICE', 'bounded-dispatch-fixture')
    app = create_dispatcher(restricted, cloud=cloud, markers=markers)
    with TestClient(app) as client:
        with psycopg.connect(settings.database_url) as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(hashtext('research-dispatch-admission'))")
            started = time.monotonic()
            result = client.post('/dispatch', json={})
            assert result.status_code == 503
            assert 2 <= time.monotonic() - started < 8
            assert not cloud.launches
            with observer.connection() as connection:
                assert connection.execute('SELECT launch_attempts FROM research.dispatch_outbox WHERE run_id=%s', (run_id,)).fetchone()['launch_attempts'] == 0
                assert connection.execute('SELECT count(*) AS n FROM research.execution_reservations WHERE run_id=%s', (run_id,)).fetchone()['n'] == 0
        with app.state.pool.connection() as connection:
            assert connection.execute('SELECT current_user AS actor').fetchone()['actor'] == 'research_dispatcher'
        assert client.post('/dispatch', json={}).status_code == 200
        assert len(cloud.launches) == 1


@pytest.mark.parametrize('finish', ['timeout', 'success', 'exception'])
def test_watchdog_terminates_or_cancels_in_a_real_process(finish):
    code = '''
import sys, time
from service.dispatcher import dispatch_deadline
try:
    with dispatch_deadline(0.1):
        if sys.argv[1] == 'timeout':
            time.sleep(30)
        if sys.argv[1] == 'exception':
            raise ValueError('fixture')
except ValueError:
    pass
time.sleep(0.25)
'''
    environment = {key: value for key, value in os.environ.items() if not key.startswith('RESEARCH_')}
    result = subprocess.run([sys.executable, '-c', code, finish],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, timeout=5)
    assert result.returncode == (124 if finish == 'timeout' else 0), result.stderr.decode(errors='replace')
