"""Database-authoritative dispatch. Cloud delivery is only a repeatable wakeup."""
import logging
import json
import secrets


OUTSTANDING = ('launching', 'uncertain', 'running')
TERMINAL_QUERY = ('complete', 'cancelled', 'failed', 'resource_limited')


class LaunchRejected(Exception):
    """A definitive cloud rejection: no execution was created."""


def enqueue(conn, run_id, settings, markers=None):
    """Call in the acceptance transaction, once for each new search generation."""
    run = conn.execute("""UPDATE research.query_runs SET dispatch_generation=dispatch_generation+1
        WHERE id=%s AND state='queued' RETURNING dispatch_generation""", (run_id,)).fetchone()
    if run is None:
        raise ValueError('Only queued work can be dispatched')
    generation = run['dispatch_generation']
    conn.execute("""INSERT INTO research.dispatch_outbox(run_id,generation) VALUES(%s,%s)
        ON CONFLICT(run_id) DO UPDATE SET generation=EXCLUDED.generation,state='pending',
        launch_attempts=0,eligible_at=now(),created_at=now()""", (run_id, generation))
    if settings.deployment_mode == 'serverless':
        from .markers import MarkerUnavailable, prepare
        if markers is None:
            raise MarkerUnavailable('Dispatch wakeup storage is not configured.')
        prepare(conn, markers, run_id, generation)
    return generation


def reserve(pool, settings):
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('research-dispatch-admission',))
        if conn.execute('SELECT paused FROM research.dispatch_control WHERE singleton').fetchone()['paused']:
            return None
        outstanding = conn.execute("SELECT count(*) AS n FROM research.execution_reservations WHERE state=ANY(%s)", (list(OUTSTANDING),)).fetchone()['n']
        launches = conn.execute("SELECT count(*) AS n FROM research.execution_reservations WHERE created_at>now()-interval '1 minute'").fetchone()['n']
        if outstanding >= min(settings.max_outstanding, settings.worker_concurrency) or launches >= settings.max_launches_per_minute:
            return None
        row = conn.execute("""SELECT o.* FROM research.dispatch_outbox o
            JOIN research.query_runs r ON r.id=o.run_id AND r.dispatch_generation=o.generation
            WHERE o.state='pending' AND o.eligible_at<=now() AND r.state='queued'
            AND r.attempts<%s AND o.launch_attempts<%s
            AND EXISTS(SELECT 1 FROM research.query_subscriptions s WHERE s.run_id=r.id AND s.active)
            AND NOT EXISTS(SELECT 1 FROM research.execution_reservations e WHERE e.run_id=r.id AND e.state=ANY(%s))
            ORDER BY o.created_at,o.run_id FOR UPDATE OF o,r SKIP LOCKED LIMIT 1""",
            (settings.max_attempts, settings.max_launch_attempts, list(OUTSTANDING))).fetchone()
        if row is None:
            return None
        reservation = conn.execute("""INSERT INTO research.execution_reservations(id,run_id,generation,state)
            VALUES(%s,%s,%s,'launching') RETURNING *""", (secrets.token_hex(16), row['run_id'], row['generation'])).fetchone()
        conn.execute("UPDATE research.dispatch_outbox SET state='reserved',launch_attempts=launch_attempts+1 WHERE run_id=%s", (row['run_id'],))
        return reservation


def settle(conn, reservation, settings, outcome):
    """Only cloud-proven terminal/rejected executions release capacity."""
    changed = conn.execute("""UPDATE research.execution_reservations SET state=%s,outcome=%s,checked_at=now()
        WHERE id=%s AND state=ANY(%s) RETURNING id""", ('rejected' if outcome == 'rejected' else 'terminal', outcome, reservation['id'], list(OUTSTANDING))).fetchone()
    if changed is None:
        return
    run = conn.execute('SELECT * FROM research.query_runs WHERE id=%s FOR UPDATE', (reservation['run_id'],)).fetchone()
    if run is None or run['dispatch_generation'] != reservation['generation']:
        return
    outbox = conn.execute('SELECT * FROM research.dispatch_outbox WHERE run_id=%s FOR UPDATE', (run['id'],)).fetchone()
    if run['state'] in TERMINAL_QUERY:
        conn.execute("UPDATE research.dispatch_outbox SET state='done' WHERE run_id=%s", (run['id'],))
    elif run['attempts'] >= settings.max_attempts or outbox['launch_attempts'] >= settings.max_launch_attempts:
        conn.execute("""UPDATE research.query_runs SET state='failed',lease_owner=NULL,lease_until=NULL,
            error='Search execution retry budget exhausted. Retry the saved query after service recovery.',
            finished_at=now(),expires_at=now()+(%s*interval '1 day') WHERE id=%s""", (settings.result_retention_days, run['id']))
        conn.execute("UPDATE research.dispatch_outbox SET state='done' WHERE run_id=%s", (run['id'],))
    else:
        conn.execute("""UPDATE research.query_runs SET state='queued',lease_owner=NULL,lease_until=NULL
            WHERE id=%s""", (run['id'],))
        conn.execute("UPDATE research.dispatch_outbox SET state='pending',eligible_at=now()+interval '15 seconds' WHERE run_id=%s", (run['id'],))


def reconcile(pool, settings, cloud):
    with pool.connection() as conn:
        rows = conn.execute("""SELECT * FROM research.execution_reservations WHERE state=ANY(%s)
            ORDER BY checked_at NULLS FIRST,created_at LIMIT %s""", (list(OUTSTANDING), settings.max_outstanding)).fetchall()
    for reservation in rows:
        try:
            status = cloud.observe(reservation)
        except Exception:
            logging.warning('Dispatch observation unavailable reservation=%s', reservation['id'])
            continue
        with pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('research-dispatch-admission',))
            current = conn.execute('SELECT * FROM research.execution_reservations WHERE id=%s FOR UPDATE', (reservation['id'],)).fetchone()
            if current['state'] not in OUTSTANDING:
                continue
            if status['state'] in ('terminal', 'rejected'):
                settle(conn, current, settings, status['state'])
            else:
                conn.execute("""UPDATE research.execution_reservations SET state=%s,
                    execution_name=COALESCE(%s,execution_name),checked_at=now() WHERE id=%s""",
                    ('running' if status['state'] == 'running' else 'uncertain', status.get('execution_name'), current['id']))
    with pool.connection() as conn:
        conn.execute("""UPDATE research.dispatch_outbox o SET state='done' FROM research.query_runs r
            WHERE r.id=o.run_id AND r.state=ANY(%s) AND o.state='pending'""", (list(TERMINAL_QUERY),))


def dispatch_once(pool, settings, cloud):
    reconcile(pool, settings, cloud)
    launched = 0
    for _ in range(settings.dispatch_batch_size):
        reservation = reserve(pool, settings)
        if reservation is None:
            break
        try:
            operation = cloud.launch(reservation)
        except LaunchRejected:
            with pool.connection() as conn:
                conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('research-dispatch-admission',))
                settle(conn, reservation, settings, 'rejected')
        except Exception:
            with pool.connection() as conn:
                conn.execute("UPDATE research.execution_reservations SET state='uncertain' WHERE id=%s AND state='launching'", (reservation['id'],))
            logging.warning('Dispatch acknowledgement uncertain reservation=%s', reservation['id'])
        else:
            with pool.connection() as conn:
                conn.execute("UPDATE research.execution_reservations SET operation_name=%s WHERE id=%s", (operation, reservation['id']))
            launched += 1
    with pool.connection() as conn:
        health = conn.execute("""SELECT
            (SELECT count(*) FROM research.dispatch_outbox WHERE state='pending') AS pending,
            (SELECT count(*) FROM research.execution_reservations WHERE state='uncertain') AS uncertain,
            (SELECT count(*) FROM research.execution_reservations WHERE state=ANY(%s)) AS outstanding,
            (SELECT count(*) FROM research.query_runs WHERE state='running' AND lease_until<now()) AS expired_leases,
            (SELECT COALESCE(extract(epoch FROM now()-min(o.created_at)),0)::integer
             FROM research.dispatch_outbox o JOIN research.query_runs r ON r.id=o.run_id AND r.dispatch_generation=o.generation
             WHERE r.state='queued') AS queue_age_seconds""",
            (list(OUTSTANDING),)).fetchone()
    print(json.dumps({'event': 'research_dispatch', 'launches': launched, **health}), flush=True)
    return {'launches': launched, **health}
