"""Leased Postgres queue consumer; run independently from the HTTP service."""
import argparse
import hashlib
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from contextlib import ExitStack

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from .app import canonical
from .config import Settings, CapacityUnavailable, check_capacity

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from solver import solve, SOLVER_VERSION


class LeaseLost(Exception):
    pass


def claim(pool, settings):
    if settings.deployment_mode == 'serverless':
        raise ValueError('Serverless workers must bind to a dispatch reservation')
    try:
        check_capacity(settings)
    except CapacityUnavailable:
        return None
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('research-worker-admission',))
        conn.execute("""UPDATE research.query_runs SET state='failed',error='Worker retry limit reached; results are incomplete.',
            finished_at=now(),lease_owner=NULL,lease_until=NULL,expires_at=now()+(%s*interval '1 day')
            WHERE state='running' AND lease_until<now() AND attempts>=%s""",(settings.result_retention_days,settings.max_attempts))
        running = conn.execute("SELECT count(*) AS total FROM research.query_runs WHERE state='running' AND lease_until>=now()").fetchone()['total']
        if running >= settings.worker_concurrency:
            return None
        run = conn.execute("""SELECT r.* FROM research.query_runs r JOIN research.publications p ON p.id=r.publication_id
            WHERE p.status='published' AND (r.state='queued' OR (r.state='running' AND r.lease_until<now()))
            AND EXISTS (SELECT 1 FROM research.query_subscriptions s WHERE s.run_id=r.id AND s.active)
            ORDER BY r.created_at,r.id FOR UPDATE OF r SKIP LOCKED LIMIT 1""").fetchone()
        if run is None:
            return None
        owner=secrets.token_hex(24)
        run=conn.execute("""UPDATE research.query_runs SET state='running',attempts=attempts+1,started_at=now(),finished_at=NULL,
            lease_owner=%s,lease_until=now()+interval '30 seconds',result_count=0,result_generation_id=NULL,champion_set_count=NULL,progress='{}',error=NULL WHERE id=%s RETURNING *""",(owner,run['id'])).fetchone()
        conn.execute('DELETE FROM research.query_results WHERE run_id=%s',(run['id'],))
        rules = conn.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND version=%s',(run['publication_id'],run['ruleset_version'])).fetchone()
        run.update(lease_owner=owner,rules=rules['rules'])
        return run


def claim_bound(pool, settings, run_id, generation, reservation_id):
    """A repeated execution cannot claim this attempt, or any other query."""
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('research-worker-admission',))
        reservation = conn.execute("""SELECT * FROM research.execution_reservations
            WHERE id=%s AND run_id=%s AND generation=%s FOR UPDATE""", (reservation_id, run_id, generation)).fetchone()
        if reservation is None or reservation['state'] not in ('launching', 'uncertain', 'running') or reservation['claimed_at']:
            return None
        running = conn.execute("SELECT count(*) AS n FROM research.query_runs WHERE state='running' AND lease_until>=now()").fetchone()['n']
        if running >= settings.worker_concurrency:
            return None
        run = conn.execute("""SELECT r.* FROM research.query_runs r JOIN research.publications p ON p.id=r.publication_id
            WHERE r.id=%s AND r.dispatch_generation=%s AND r.state='queued' AND r.attempts<%s
            AND p.status='published' AND EXISTS(SELECT 1 FROM research.query_subscriptions s WHERE s.run_id=r.id AND s.active)
            FOR UPDATE OF r""", (run_id, generation, settings.max_attempts)).fetchone()
        if run is None:
            return None
        owner = secrets.token_hex(24)
        run = conn.execute("""UPDATE research.query_runs SET state='running',attempts=attempts+1,started_at=now(),finished_at=NULL,
            lease_owner=%s,lease_until=now()+interval '30 seconds',result_count=0,champion_set_count=NULL,
            progress='{}',error=NULL,result_generation_id=NULL WHERE id=%s RETURNING *""", (owner, run_id)).fetchone()
        conn.execute("UPDATE research.execution_reservations SET claimed_at=now(),state='running' WHERE id=%s", (reservation_id,))
        conn.execute('DELETE FROM research.query_results WHERE run_id=%s', (run_id,))
        rules = conn.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND version=%s',
                             (run['publication_id'], run['ruleset_version'])).fetchone()
        run.update(rules=rules['rules'], reservation_id=reservation_id)
        return run


class LeaseHeartbeat:
    """Renew while Numba compiles and while result batches are persisted."""
    def __init__(self, pool, run):
        self.pool, self.run = pool, run
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self.renew, name='search-lease', daemon=True)

    def renew(self):
        while not self.stopped.wait(5):
            try:
                with self.pool.connection() as conn:
                    owned = conn.execute("""UPDATE research.query_runs SET lease_until=now()+interval '30 seconds'
                        WHERE id=%s AND lease_owner=%s AND state='running' AND lease_until>=now() RETURNING id""",
                        (self.run['id'], self.run['lease_owner'])).fetchone()
                if owned is None:
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=10)


def execute(pool, settings, run):
    with ExitStack() as resources:
        budget = None
        if settings.budget_window_id:
            from .budget import Budget
            budget_pool = resources.enter_context(ConnectionPool(settings.database_url, min_size=0, max_size=1, timeout=3,
                kwargs={'row_factory': dict_row, 'connect_timeout': 5, 'options': '-c statement_timeout=5000 -c lock_timeout=3000'},
                check=ConnectionPool.check_connection))
            budget = Budget(budget_pool, settings.budget_window_id)
        with LeaseHeartbeat(pool, run) as heartbeat:
            return execute_leased(pool, settings, run, heartbeat, budget=budget)


def execute_leased(pool, settings, run, heartbeat, *, budget=None):
    from .result_store import ResultWriter, ResultLeaseLost
    from .result_builder import ScratchLimitExceeded
    batch=[]
    last_check=0
    interrupted=False
    progress={}
    writer=None

    def flush(force=False):
        nonlocal last_check,interrupted
        if heartbeat.lost.is_set():
            raise LeaseLost()
        if not force and time.monotonic()-last_check<1 and len(batch)<1000:
            return
        check_capacity(settings)
        if writer is not None:
            writer.append(batch, progress)
            batch.clear()
            last_check=time.monotonic()
            return
        with pool.connection() as conn:
            owned=conn.execute("""UPDATE research.query_runs SET lease_until=now()+interval '30 seconds',progress=%s
                WHERE id=%s AND lease_owner=%s AND state='running' AND lease_until>=now() RETURNING id""",
                (Jsonb(progress),run['id'],run['lease_owner'])).fetchone()
            if owned is None:
                interrupted=True
                raise LeaseLost()
            if batch:
                with conn.cursor() as cursor:
                    cursor.executemany('''INSERT INTO research.query_results(run_id,configuration_key,champion_set_key,
                        active_trait_count,total_cost,champion_names,result) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(run_id,configuration_key) DO NOTHING''',[
                        (run['id'],result['configuration_key'],result.get('champion_set_key') or hashlib.sha256(canonical(sorted(member['champion_id'] for member in result['members'])).encode()).hexdigest(),
                         result['active_trait_count'],result['total_cost'],result.get('names_sort') or '\x1f'.join(sorted(member['name'] for member in result['members'])),Jsonb(result)) for result in batch])
                    inserted=cursor.rowcount
                conn.execute('UPDATE research.query_runs SET result_count=result_count+%s WHERE id=%s',(inserted,run['id']))
        batch.clear()
        last_check=time.monotonic()

    def on_result(result):
        batch.append(result)
        flush()

    def on_progress(value):
        nonlocal progress
        progress=value
        flush()

    def cancelled():
        flush()
        return interrupted

    try:
        if settings.result_storage_mode == 'objects':
            writer = ResultWriter(pool, settings, run, heartbeat, budget=budget)
        flush(force=True)
        if run['solver_version']!=SOLVER_VERSION:
            raise ValueError('Historical solver version is unavailable. Deploy its archived worker to recompute.')
        summary=solve(run['definition'],run['rules'],on_result=on_result,on_progress=on_progress,cancelled=cancelled,
                      max_seconds=settings.max_seconds,max_results=settings.max_results)
        progress=summary
        flush(force=True)
        if writer is not None:
            writer.finish(summary)
            return summary['state']
        with pool.connection() as conn:
            owned = conn.execute("""SELECT id FROM research.query_runs WHERE id=%s AND state='running'
                AND lease_owner=%s AND lease_until>=now() FOR UPDATE""", (run['id'], run['lease_owner'])).fetchone()
            if owned is None or heartbeat.lost.is_set():
                raise LeaseLost()
            counts=conn.execute('SELECT count(*) AS configurations,count(DISTINCT champion_set_key) AS champion_sets FROM research.query_results WHERE run_id=%s',(run['id'],)).fetchone()
            if summary['complete'] and counts['configurations']!=summary['configuration_count']:
                raise ValueError('Stored result count differs from solver validation; completion rejected.')
            conn.execute("""UPDATE research.query_runs SET state=%s,progress=%s,result_count=%s,champion_set_count=%s,
                finished_at=now(),lease_owner=NULL,lease_until=NULL,error=%s,expires_at=now()+(%s*interval '1 day')
                WHERE id=%s AND state='running' AND lease_owner=%s""",
                (summary['state'],Jsonb(summary),counts['configurations'],counts['champion_sets'],summary.get('reason'),settings.result_retention_days,run['id'],run['lease_owner']))
        return summary['state']
    except (CapacityUnavailable, ScratchLimitExceeded):
        with pool.connection() as conn:
            conn.execute("""UPDATE research.query_runs SET state='resource_limited',finished_at=now(),lease_owner=NULL,lease_until=NULL,
                expires_at=now()+(%s*interval '1 day'),
                error='Search stopped because disk capacity is unavailable. Results are incomplete; retry after capacity recovers.'
                WHERE id=%s AND state='running' AND lease_owner=%s""",(settings.result_retention_days,run['id'],run['lease_owner']))
        return 'resource_limited'
    except (LeaseLost, ResultLeaseLost):
        return 'lease_lost'
    except (psycopg.OperationalError, psycopg.InterfaceError, PoolTimeout):
        # Platform retries are disabled. Reconciliation retries only after proving
        # this execution terminal, and charges the same finite attempt budget.
        raise
    except Exception:
        # Errors can contain connection credentials; only the exception category enters public state.
        with pool.connection() as conn:
            conn.execute("""UPDATE research.query_runs SET state='failed',finished_at=now(),lease_owner=NULL,lease_until=NULL,
                expires_at=now()+(%s*interval '1 day'),
                error='Worker execution failed. Results are incomplete; retry the saved query or contact the operator.'
                WHERE id=%s AND state='running' AND lease_owner=%s""",(settings.result_retention_days,run['id'],run['lease_owner']))
        logging.error('Query worker failed for run %s',run['id'])
        return 'failed'
    finally:
        if writer is not None:
            writer.close()


def cleanup(pool,settings=None):
    settings=settings or Settings.from_env()
    if settings.artifact_root or settings.supabase_url and settings.supabase_key:
        from .export_artifacts import delete_expired
        delete_expired(pool,settings)
    with pool.connection() as conn:
        conn.execute("DELETE FROM research.request_limits WHERE window_start<now()-interval '1 day'")
        conn.execute('DELETE FROM research.query_results WHERE run_id IN (SELECT id FROM research.query_runs WHERE expires_at<now())')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true',help='Process at most one job and exit')
    parser.add_argument('--cleanup',action='store_true',help='Remove expired result rows and quota counters')
    parser.add_argument('--bound',action='store_true',help='Execute only the dispatched run, generation and reservation')
    args=parser.parse_args()
    settings=Settings.from_env()
    if not settings.database_url:
        parser.error('RESEARCH_DATABASE_URL is required')
    if settings.deployment_mode == 'serverless' and not (args.bound or args.cleanup):
        parser.error('Serverless workers require --bound; use a separate operator Job for cleanup')
    with ConnectionPool(settings.database_url,min_size=1,max_size=1 if settings.budget_window_id else 2,timeout=3,
                        kwargs={'row_factory':dict_row, 'connect_timeout': 5, 'options': '-c statement_timeout=15000 -c lock_timeout=10000'},
                        check=ConnectionPool.check_connection) as pool:
        if args.cleanup:
            cleanup(pool,settings)
            return
        if args.bound:
            try:
                run_id = os.environ['RESEARCH_RUN_ID']
                generation = int(os.environ['RESEARCH_GENERATION'])
                reservation_id = os.environ['RESEARCH_RESERVATION_ID']
                if not run_id or generation < 1 or not reservation_id:
                    raise ValueError()
            except (KeyError, ValueError):
                parser.error('Bound execution requires run ID, positive generation and reservation ID')
            run = claim_bound(pool, settings, run_id, generation, reservation_id)
            if run:
                outcome = execute(pool, settings, run)
                if outcome in ('failed', 'resource_limited'):
                    raise SystemExit(2)
                if outcome == 'lease_lost':
                    with pool.connection() as conn:
                        state = conn.execute('SELECT state FROM research.query_runs WHERE id=%s', (run_id,)).fetchone()
                    if state['state'] != 'cancelled':
                        raise SystemExit(75)
            return
        while True:
            run=claim(pool,settings)
            if run:
                execute(pool,settings,run)
            if args.once:
                return
            if run is None:
                time.sleep(2)


if __name__=='__main__':
    main()
