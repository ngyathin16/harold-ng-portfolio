"""Recompute the two saved release queries in an explicitly isolated restore database."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
import psutil
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from solver import evaluate


QUERIES = [
    ('general', 'LoDake-URTpPoMjkuyoLvrRz', 280543, 211961),
    ('jazz', 'RtsededKBFy4Ve7lQleExbjL', 38809, 30935),
]


def recount(connection, identity, definition, rules):
    checked = 0
    with connection.cursor(name='recovered_result_recount') as rows:
        rows.execute('SELECT result FROM research.query_results WHERE run_id=%s ORDER BY configuration_key', (identity,))
        for row in rows:
            stored = row['result']
            result = evaluate([member['form_id'] for member in stored['members']], definition, rules)
            if result != stored:
                raise ValueError('Independent plain-Python recount differs from a stored configuration')
            checked += 1
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Expire and recompute only the two named restored caches')
    parser.add_argument('--api', default='http://127.0.0.1:8789')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/capacity-worker.json')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required; this check replaces only isolated restored query results')
    if urlsplit(args.api).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('This recovery verification requires a loopback API')
    database_url = os.environ.get('RESEARCH_RESTORE_DATABASE_URL')
    if not database_url:
        parser.error('RESEARCH_RESTORE_DATABASE_URL is required')
    cache = ROOT / '.local' / 'capacity-numba' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    cache.mkdir(parents=True, exist_ok=False)
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'Sequential uncached query recovery on local x86 Windows; not hosted concurrency or ARM capacity',
        'machine': {
            'os': platform.platform(), 'architecture': platform.machine(), 'processor': platform.processor(),
            'physical_cpus': psutil.cpu_count(logical=False), 'logical_cpus': psutil.cpu_count(),
            'memory_total_bytes': psutil.virtual_memory().total,
            'memory_available_at_start_bytes': psutil.virtual_memory().available,
        },
        'versions': {'python': platform.python_version(), **{
            name: importlib.metadata.version(name) for name in ('numpy', 'numba', 'psycopg', 'psutil')
        }},
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in [ROOT/'service/worker.py', ROOT/'solver/search.py', ROOT/'solver/native.py']},
        'settings': {'max_seconds': 120, 'max_results': 1000000, 'worker_concurrency': 1, 'storage_configured': False},
        'measurement': {
            'process_peak_rss': 'Maximum observed sum of resident sets across the worker launch process and its descendants, sampled every 50 ms. Includes the Windows virtualenv redirector and its actual Python worker. Shared pages can be counted by multiple processes. Excludes PostgreSQL, API, verifier, and filesystem cache.',
            'process_elapsed': 'Worker startup, imports, compilation/cache load, enumeration, independent solver recount, and PostgreSQL persistence.',
            'solver_elapsed': 'Reported by solver; includes callback persistence and compilation after solver timer starts.',
            'compile_time': 'Not isolated by current solver instrumentation. First run starts with an empty dedicated Numba cache; the second uses the populated cache.',
        },
        'runs': [], 'passed': False,
    }
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as conn, httpx.Client(base_url=args.api, timeout=30) as api:
            current = conn.execute('SELECT current_database() AS name,version() AS version').fetchone()
            if current['name'] != 'research_capacity_restore_test':
                raise ValueError('Refusing to alter anything except research_capacity_restore_test')
            report['database'] = current['name']
            report['versions']['postgres'] = current['version']
            pending = conn.execute("SELECT id,state FROM research.query_runs WHERE state IN ('queued','running')").fetchall()
            if any(row['state']=='running' or row['id'] not in {query[1] for query in QUERIES} for row in pending):
                raise ValueError('The isolated queue contains running work or an unrelated queued query')
            for index, (name, identity, expected_count, expected_champions) in enumerate(QUERIES):
                row = conn.execute('''SELECT r.*,s.rules FROM research.query_runs r JOIN research.rulesets s
                    ON s.publication_id=r.publication_id AND s.version=r.ruleset_version WHERE r.id=%s''', (identity,)).fetchone()
                if row is None:
                    raise ValueError('The named saved query is missing from the recovered database')
                before = api.get('/v1/queries/' + identity)
                before.raise_for_status()
                if before.json()['query'] != row['definition']:
                    raise ValueError('API query does not match the isolated restored definition')
                if row['state'] != 'queued':
                    conn.execute("UPDATE research.query_runs SET expires_at=now()-interval '1 second' WHERE id=%s", (identity,))
                    conn.commit()
                    if api.get('/v1/queries/' + identity).json()['expired'] is not True:
                        raise ValueError('API did not observe the isolated database cache expiry')
                response = api.post('/v1/queries', json={'publication_id': row['publication_id'], 'query': row['definition']})
                response.raise_for_status()
                if response.json()['query_id'] != identity or response.json()['state'] != 'queued':
                    raise ValueError('Submission did not requeue the saved query identity')
                deadline = time.monotonic() + 5
                while True:
                    count = conn.execute('SELECT count(*) AS n FROM research.query_results WHERE run_id=%s', (identity,)).fetchone()['n']
                    conn.commit()
                    if count == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError('Recomputation started with existing result rows')
                    time.sleep(0.05)
                env = {key: value for key, value in os.environ.items() if not key.startswith('RESEARCH_')}
                env.update(RESEARCH_DATABASE_URL=database_url, RESEARCH_WORKER_CONCURRENCY='1',
                           RESEARCH_MAX_SECONDS='120', RESEARCH_MAX_RESULTS='1000000', NUMBA_CACHE_DIR=str(cache))
                print('Starting uncached ' + name + ' query', flush=True)
                started = time.monotonic()
                peak = 0
                largest_process = 0
                observed_processes = set()
                log = ROOT / '.local' / ('capacity-worker-' + name + '.log')
                with log.open('w', encoding='utf-8') as output:
                    process = subprocess.Popen([sys.executable, '-m', 'service.worker', '--once'], cwd=ROOT,
                                               env=env, stdout=output, stderr=subprocess.STDOUT)
                    monitor = psutil.Process(process.pid)
                    while process.poll() is None:
                        try:
                            resident = []
                            for child in [monitor, *monitor.children(recursive=True)]:
                                try:
                                    resident.append(child.memory_info().rss)
                                    observed_processes.add(child.pid)
                                except psutil.NoSuchProcess:
                                    continue
                            peak = max(peak, sum(resident))
                            largest_process = max([largest_process, *resident])
                        except psutil.NoSuchProcess:
                            break
                        if time.monotonic() - started > 300:
                            process.kill()
                            process.wait()
                            raise ValueError('Worker exceeded the outer 300-second verification deadline')
                        time.sleep(0.05)
                    code = process.wait()
                elapsed = time.monotonic() - started
                status = api.get('/v1/queries/' + identity)
                status.raise_for_status()
                status = status.json()
                counts = conn.execute('''SELECT count(*) AS configurations,count(DISTINCT champion_set_key) AS champion_sets
                    FROM research.query_results WHERE run_id=%s''', (identity,)).fetchone()
                run = {'name': name, 'query_id': identity, 'query': row['definition'], 'solver_version': row['solver_version'],
                       'cache': 'empty result cache; cold Numba cache' if index == 0 else 'empty result cache; populated Numba cache',
                       'process_exit_code': code, 'process_elapsed_seconds': elapsed, 'peak_rss_bytes': peak,
                       'largest_individual_process_rss_bytes': largest_process, 'observed_worker_processes': len(observed_processes),
                       'state': status['state'], 'complete': status['complete'], 'reported_count': status['result_count'],
                       'reported_champion_sets': status['champion_set_count'], 'stored_counts': counts,
                       'expected_count': expected_count, 'expected_champion_sets': expected_champions,
                       'progress': status['progress'], 'saved_query_id_preserved': True,
                       'export_metadata_absent': 'exports' not in status,
                       'query_artifacts': conn.execute('SELECT count(*) AS n FROM research.query_artifacts WHERE run_id=%s', (identity,)).fetchone()['n']}
                report['runs'].append(run)
                if code != 0 or not status['complete'] or counts != {'configurations': expected_count, 'champion_sets': expected_champions}:
                    raise ValueError('Recovered query did not finish exhaustively with the recorded baseline counts')
                if status['result_count'] != expected_count or status['champion_set_count'] != expected_champions:
                    raise ValueError('Public query statistics differ from stored rows')
                if not run['export_metadata_absent'] or run['query_artifacts'] != 0:
                    raise ValueError('Query unexpectedly required or produced public export objects')
                recount_start = time.monotonic()
                run['independently_recounted_rows'] = recount(conn, identity, row['definition'], row['rules'])
                run['post_persistence_recount_seconds'] = time.monotonic() - recount_start
                run['passed'] = run['independently_recounted_rows'] == expected_count
                conn.commit()
                print(name + ': complete; independent recount passed', flush=True)
            report['jazz_not_in_general'] = conn.execute('''SELECT count(*) AS n FROM research.query_results jazz
                WHERE jazz.run_id=%s AND NOT EXISTS(SELECT 1 FROM research.query_results general
                    WHERE general.run_id=%s AND general.configuration_key=jazz.configuration_key)''', (QUERIES[1][1], QUERIES[0][1])).fetchone()['n']
            report['database_bytes_after'] = conn.execute('SELECT pg_database_size(current_database()) AS bytes').fetchone()['bytes']
            report['passed'] = report['jazz_not_in_general'] == 0 and all(run['passed'] for run in report['runs'])
            if not report['passed']:
                raise ValueError('Recovered Jazz configurations differ from the general query scope')
    except Exception as error:
        report['failure'] = type(error).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'runs': len(report['runs']), 'jazz_not_in_general': report['jazz_not_in_general']}))


if __name__ == '__main__':
    main()
