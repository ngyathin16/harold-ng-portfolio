"""Leased Postgres queue consumer; run independently from the HTTP service."""
import argparse
import hashlib
import logging
from pathlib import Path
import secrets
import sys
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .app import canonical
from .config import Settings

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from solver import solve, SOLVER_VERSION


class LeaseLost(Exception):
    pass


def claim(pool, settings):
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
            lease_owner=%s,lease_until=now()+interval '30 seconds',result_count=0,champion_set_count=NULL,progress='{}',error=NULL WHERE id=%s RETURNING *""",(owner,run['id'])).fetchone()
        conn.execute('DELETE FROM research.query_results WHERE run_id=%s',(run['id'],))
        rules = conn.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND version=%s',(run['publication_id'],run['ruleset_version'])).fetchone()
        run.update(lease_owner=owner,rules=rules['rules'])
        return run


def execute(pool, settings, run):
    batch=[]
    last_check=0
    interrupted=False
    progress={}

    def flush(force=False):
        nonlocal last_check,interrupted
        if not force and time.monotonic()-last_check<1 and len(batch)<1000:
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
        if run['solver_version']!=SOLVER_VERSION:
            raise ValueError('Historical solver version is unavailable. Deploy its archived worker to recompute.')
        summary=solve(run['definition'],run['rules'],on_result=on_result,on_progress=on_progress,cancelled=cancelled,
                      max_seconds=settings.max_seconds,max_results=settings.max_results)
        progress=summary
        flush(force=True)
        with pool.connection() as conn:
            counts=conn.execute('SELECT count(*) AS configurations,count(DISTINCT champion_set_key) AS champion_sets FROM research.query_results WHERE run_id=%s',(run['id'],)).fetchone()
            if summary['complete'] and counts['configurations']!=summary['configuration_count']:
                raise ValueError('Stored result count differs from solver validation; completion rejected.')
            conn.execute("""UPDATE research.query_runs SET state=%s,progress=%s,result_count=%s,champion_set_count=%s,
                finished_at=now(),lease_owner=NULL,lease_until=NULL,error=%s,expires_at=now()+(%s*interval '1 day')
                WHERE id=%s AND state='running' AND lease_owner=%s""",
                (summary['state'],Jsonb(summary),counts['configurations'],counts['champion_sets'],summary.get('reason'),settings.result_retention_days,run['id'],run['lease_owner']))
        if summary['complete'] and (settings.artifact_root or settings.supabase_url and settings.supabase_key):
            began=time.monotonic()
            with pool.connection() as conn:
                conn.execute("UPDATE research.query_runs SET progress=progress || %s WHERE id=%s AND started_at=%s AND state='complete'",(Jsonb({'artifact_status':'preparing'}),run['id'],run['started_at']))
            try:
                from .export_artifacts import materialize
                materialize(pool,settings,run['id'])
            except Exception:
                status='failed'
                logging.error('Stored export failed for run %s; complete streaming download remains available',run['id'])
            else:
                status='ready'
            with pool.connection() as conn:
                conn.execute("UPDATE research.query_runs SET progress=progress || %s WHERE id=%s AND started_at=%s AND state='complete'",(Jsonb({'artifact_status':status,'artifact_seconds':time.monotonic()-began}),run['id'],run['started_at']))
    except LeaseLost:
        return
    except Exception:
        # Errors can contain connection credentials; only the exception category enters public state.
        with pool.connection() as conn:
            conn.execute("""UPDATE research.query_runs SET state='failed',finished_at=now(),lease_owner=NULL,lease_until=NULL,
                expires_at=now()+(%s*interval '1 day'),
                error='Worker execution failed. Results are incomplete; retry the saved query or contact the operator.'
                WHERE id=%s AND state='running' AND lease_owner=%s""",(settings.result_retention_days,run['id'],run['lease_owner']))
        logging.error('Query worker failed for run %s',run['id'])


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
    parser.add_argument('--prepare-exports',metavar='QUERY_ID',help='Retry verified object exports for a completed query')
    args=parser.parse_args()
    settings=Settings.from_env()
    if not settings.database_url:
        parser.error('RESEARCH_DATABASE_URL is required')
    with ConnectionPool(settings.database_url,min_size=1,max_size=2,kwargs={'row_factory':dict_row}) as pool:
        if args.prepare_exports:
            from .export_artifacts import materialize
            print(canonical(materialize(pool,settings,args.prepare_exports)))
            return
        if args.cleanup:
            cleanup(pool,settings)
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
