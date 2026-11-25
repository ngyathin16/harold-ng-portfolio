"""Conservative managed-storage reservations serialized with query admission."""
from .config import CapacityUnavailable


def reserve(conn, run_id, settings):
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('research-query-admission'))")
    if conn.execute('SELECT paused FROM research.dispatch_control WHERE singleton').fetchone()['paused']:
        raise CapacityUnavailable('New searches are paused by the operator. Saved work and browsing remain available.')
    if conn.execute("""SELECT 1 FROM research.execution_reservations WHERE run_id=%s
        AND state IN ('launching','uncertain','running') LIMIT 1""", (run_id,)).fetchone():
        raise CapacityUnavailable('The previous execution is still being reconciled. Retry this saved query shortly.')
    from .capacity import refresh
    refresh(conn, settings)
    sample = conn.execute('''SELECT *, measured_at <= clock_timestamp() AND
        measured_at >= clock_timestamp()-(%s*interval '1 second') AS fresh
        FROM research.capacity_sample WHERE singleton''', (settings.capacity_max_age_seconds,)).fetchone()
    if not sample or not sample['fresh']:
        raise CapacityUnavailable('Search capacity measurement is unavailable or stale. Browsing remains available; retry later.')
    reserved = conn.execute('SELECT COALESCE(sum(bytes),0) AS total FROM research.capacity_reservations').fetchone()['total']
    if sample['used_bytes'] + reserved + settings.result_reservation_bytes + settings.database_headroom_bytes > sample['limit_bytes']:
        raise CapacityUnavailable('Team search is at managed database capacity. Browsing remains available; retry later.')
    conn.execute('''INSERT INTO research.capacity_reservations(run_id,bytes) VALUES(%s,%s)
        ON CONFLICT(run_id) DO UPDATE SET bytes=research.capacity_reservations.bytes+excluded.bytes''',
        (run_id, settings.result_reservation_bytes))


def record_sample(conn, used_bytes, limit_bytes, measured_at=None):
    """Operator supplies provider-accounted bytes including branches/history, not dump size."""
    if not isinstance(used_bytes, int) or not isinstance(limit_bytes, int) or used_bytes < 0 or limit_bytes <= 0:
        raise ValueError('Capacity requires nonnegative usage and a positive provider limit')
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('research-query-admission'))")
    observed = conn.execute('SELECT COALESCE(%s::timestamptz,now()) AS stamp', (measured_at,)).fetchone()['stamp']
    previous = conn.execute('SELECT * FROM research.capacity_sample WHERE singleton').fetchone()
    if previous and observed <= previous['measured_at']:
        if observed == previous['measured_at'] and (used_bytes, limit_bytes) == (previous['used_bytes'], previous['limit_bytes']):
            return
        raise ValueError('Capacity measurements must advance monotonically')
    conn.execute('''INSERT INTO research.capacity_sample(singleton,used_bytes,limit_bytes,measured_at)
        VALUES(true,%s,%s,COALESCE(%s,now())) ON CONFLICT(singleton) DO UPDATE
        SET used_bytes=excluded.used_bytes,limit_bytes=excluded.limit_bytes,measured_at=excluded.measured_at''',
        (used_bytes, limit_bytes, observed))
    # Retain active allocations even when the latest measurement includes some of
    # their writes. Double counting is safe; an optimistic release is not.
    conn.execute('''DELETE FROM research.capacity_reservations c USING research.query_runs r
        WHERE c.run_id=r.id AND r.state NOT IN ('queued','running')
        AND r.finished_at <= (SELECT measured_at FROM research.capacity_sample WHERE singleton)
        AND NOT EXISTS (SELECT 1 FROM research.execution_reservations e
            WHERE e.run_id=r.id AND e.state NOT IN ('terminal','rejected'))''')
