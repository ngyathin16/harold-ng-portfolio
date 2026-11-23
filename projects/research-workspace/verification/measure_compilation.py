"""Measure a cold native compilation separately from a full warm solver run."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

import psutil
import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
QUERY_ID = 'LoDake-URTpPoMjkuyoLvrRz'
sys.path.insert(0, str(ROOT))


class ArgumentsCaptured(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/capacity-compilation.json')
    args = parser.parse_args()
    database_url = os.environ.get('RESEARCH_RESTORE_DATABASE_URL')
    if not database_url:
        parser.error('RESEARCH_RESTORE_DATABASE_URL is required')
    cache_parent = ROOT / '.local/capacity-compilation'
    cache_parent.mkdir(parents=True, exist_ok=True)
    cache = Path(tempfile.mkdtemp(prefix='cold-', dir=cache_parent)).resolve()
    if not cache.is_relative_to(cache_parent.resolve()):
        raise ValueError('Compilation cache leaves the private verification directory')
    os.environ['NUMBA_CACHE_DIR'] = str(cache)
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
        'scope': 'One local x86 cold compiler initialization and one warm general-query solver run; no database writes or persistence workload.',
        'machine': {
            'os': platform.platform(), 'architecture': platform.machine(), 'processor': platform.processor(),
            'physical_cpus': psutil.cpu_count(logical=False), 'logical_cpus': psutil.cpu_count(),
            'memory_total_bytes': psutil.virtual_memory().total,
            'memory_available_at_start_bytes': psutil.virtual_memory().available,
        },
        'versions': {'python': platform.python_version(), **{
            name: importlib.metadata.version(name) for name in ('numpy', 'numba', 'llvmlite', 'psycopg', 'psutil')
        }},
        'source_sha256': {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (ROOT/'solver/search.py', ROOT/'solver/native.py')},
        'method': {
            'compile': 'Capture the real first native-call argument types without invoking the kernel, then time dispatcher.compile(signature) with an empty dedicated cache. Includes compiler/runtime initialization, machine-code generation, and cache write; excludes dependency imports and query preparation.',
            'warm_solver': 'Run the unchanged production solver after its required signature is compiled. Includes native enumeration, plain-Python validation of every result, and collecting configuration identities; excludes PostgreSQL persistence, API, worker startup, and the final identity comparison.',
            'limitations': 'Single diagnostic sample, not a repeatability benchmark, hosted latency estimate, or ARM result. The prior worker report includes persistence and cannot be compared directly with this warm solver-only timing.',
        },
    }
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            target = connection.execute('SELECT current_database() AS name,version() AS version').fetchone()
            if target['name'] != 'research_capacity_restore_test':
                raise ValueError('This diagnostic only reads research_capacity_restore_test')
            report['database'] = target['name']
            report['versions']['postgres'] = target['version']
            row = connection.execute('''SELECT r.id,r.state,r.definition,r.result_count,r.champion_set_count,r.solver_version,s.rules
                FROM research.query_runs r JOIN research.rulesets s ON s.publication_id=r.publication_id
                    AND s.version=r.ruleset_version WHERE r.id=%s''', (QUERY_ID,)).fetchone()
            if row is None or row['state'] != 'complete':
                raise ValueError('The restored general query must have a completed reference result')
            reference_hash = hashlib.sha256()
            reference_count = 0
            with connection.cursor(name='reference_configuration_keys') as cursor:
                cursor.execute('SELECT configuration_key FROM research.query_results WHERE run_id=%s ORDER BY configuration_key', (QUERY_ID,))
                for item in cursor:
                    reference_hash.update((item['configuration_key'] + '\n').encode())
                    reference_count += 1
            if reference_count != row['result_count'] or reference_count != 280543:
                raise ValueError('Restored reference counts differ from the release baseline')
            report['query_id'] = QUERY_ID
            report['query'] = row['definition']
            report['reference'] = {'configuration_count': reference_count, 'champion_set_count': row['champion_set_count'],
                                   'configuration_keys_sha256': reference_hash.hexdigest()}

        imported_at = time.perf_counter()
        import numba
        from solver import SOLVER_VERSION, solve
        from solver import native
        report['dependency_import_seconds'] = time.perf_counter() - imported_at
        dispatcher = native.advance
        if SOLVER_VERSION != row['solver_version'] or not hasattr(dispatcher, 'compile') or dispatcher.signatures:
            raise ValueError('Expected the recorded solver and a fresh JIT dispatcher')
        captured = None

        def capture(*values):
            nonlocal captured
            captured = values
            raise ArgumentsCaptured()

        prepared_at = time.perf_counter()
        native.advance = capture
        try:
            solve(row['definition'], row['rules'], max_seconds=120, max_results=1000000)
        except ArgumentsCaptured:
            pass
        finally:
            native.advance = dispatcher
        report['argument_preparation_seconds'] = time.perf_counter() - prepared_at
        if captured is None or list(cache.rglob('*.nbi')) or list(cache.rglob('*.nbc')):
            raise ValueError('Expected real kernel arguments and an empty compilation cache')
        signature = tuple(numba.typeof(value) for value in captured)
        report['native_signature'] = [str(value) for value in signature]
        started = time.perf_counter()
        dispatcher.compile(signature)
        report['cold_compile_seconds'] = time.perf_counter() - started
        signatures = tuple(dispatcher.signatures)
        if signatures != (signature,):
            raise ValueError('Compilation did not create exactly the requested specialization')
        report['cache_files_after_compile'] = len(list(cache.rglob('*.nbi'))) + len(list(cache.rglob('*.nbc')))
        if report['cache_files_after_compile'] < 2:
            raise ValueError('Fresh compilation did not write its cache index and machine code')
        print('Cold compilation measured: ' + str(round(report['cold_compile_seconds'], 6)) + ' seconds', flush=True)

        identities = []
        began = time.perf_counter()
        summary = solve(row['definition'], row['rules'], on_result=lambda result: identities.append(result['configuration_key']),
                        max_seconds=120, max_results=1000000)
        report['warm_solver_wall_seconds'] = time.perf_counter() - began
        report['warm_solver'] = summary
        report['additional_specializations_during_warm_run'] = len(dispatcher.signatures) - len(signatures)
        result_hash = hashlib.sha256()
        for identity in sorted(identities):
            result_hash.update((identity + '\n').encode())
        report['configuration_keys_sha256'] = result_hash.hexdigest()
        report['emitted_configurations'] = len(identities)
        report['passed'] = bool(summary['complete'] and summary['configuration_count'] == reference_count
                                and summary['champion_set_count'] == row['champion_set_count']
                                and len(identities) == reference_count
                                and result_hash.digest() == reference_hash.digest()
                                and tuple(dispatcher.signatures) == signatures)
        if not report['passed']:
            raise ValueError('Warm solver result or specialization differs from the validated reference')
    except Exception as error:
        report['failure'] = type(error).__name__
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'cold_compile_seconds': report['cold_compile_seconds'],
                      'warm_solver_wall_seconds': report['warm_solver_wall_seconds'], 'configurations': len(identities)}))


if __name__ == '__main__':
    main()
