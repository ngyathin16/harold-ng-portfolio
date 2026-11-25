"""Monotone resource allowances; these counters are not a provider invoice cap.

The owner installs one reviewed window and imports already-incurred usage. A new
month never resets counters automatically. Storage liability survives windows and
unknown outcomes. Only verified physical deletion and completed run cleanup can
release retained storage; monthly request and execution counters never decrease.
"""
from datetime import datetime
import re

import psycopg
from psycopg.types.json import Jsonb

from .config import CapacityUnavailable


METRICS = frozenset(('search_attempts', 'operator_executions', 'service_invocations',
    'r2_class_a', 'r2_class_b', 'google_egress_bytes', 'api_response_bytes', 'gcs_class_a', 'cloud_control_calls'))
ATTEMPT_OBJECT_BYTES = 112 * 1024 * 1024
FINAL_OBJECT_BYTES = 75_000_000 + 4 * 1024 * 1024
RETAINED_COPIES = 12  # Eleven complete backups and one isolated restore copy.
MIGRATION_COPIES = 13  # Migrated live objects, eleven backups and one isolated restore.
REQUEST_OVERHEAD_BYTES = 8192
MAX_PHYSICAL_OBJECT_BYTES = 5_000_000_000


class BudgetUnavailable(CapacityUnavailable):
    pass


def search_liability(attempts):
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError('Search attempt allowance must be between one and three')
    return attempts * ATTEMPT_OBJECT_BYTES + RETAINED_COPIES * FINAL_OBJECT_BYTES


def _amount(value):
    return type(value) is int and 0 <= value < 10**18


def install_window(conn, identity, starts_at, ends_at, limits, used, storage_ceiling,
                   storage_used, review_reference):
    """Owner-only setup in the authoritative source DB; never called by runtime.

    Existing windows cannot be overwritten. Repeating the same installation is
    rejected instead of accidentally restoring an earlier usage baseline.
    """
    if (not isinstance(identity, str) or not 1 <= len(identity) <= 128
            or set(limits) != METRICS or set(used) - METRICS
            or any(not _amount(value) for value in (*limits.values(), *used.values(), storage_ceiling, storage_used))
            or any(used.get(key, 0) > value for key, value in limits.items())
            or storage_used > storage_ceiling or not isinstance(review_reference, str) or len(review_reference) < 4):
        raise ValueError('A complete reviewed resource allowance and nonnegative usage baseline are required')
    if (not isinstance(starts_at, datetime) or not isinstance(ends_at, datetime)
            or starts_at.tzinfo is None or ends_at.tzinfo is None
            or not 0 < (ends_at-starts_at).total_seconds() <= 32*86400):
        raise ValueError('The explicit budget window must use timezone-aware dates at most 32 days apart')
    with conn.transaction():
        conn.execute('''INSERT INTO research.budget_windows(id,starts_at,ends_at,review_reference)
            VALUES(%s,%s,%s,%s)''', (identity, starts_at, ends_at, review_reference))
        for metric in sorted(METRICS):
            conn.execute('''INSERT INTO research.budget_metrics(window_id,metric,ceiling,used)
                VALUES(%s,%s,%s,%s)''', (identity, metric, limits[metric], used.get(metric, 0)))
        previous = conn.execute('SELECT ceiling,used FROM research.budget_storage WHERE singleton FOR UPDATE').fetchone()
        if previous is None:
            conn.execute('INSERT INTO research.budget_storage(singleton,ceiling,used) VALUES(true,%s,%s)',
                         (storage_ceiling, storage_used))
        elif previous['ceiling'] != storage_ceiling or previous['used'] != storage_used:
            raise ValueError('Retained liability must carry forward unchanged; audit adjustments separately')
        conn.execute('UPDATE research.budget_windows SET armed=true WHERE id=%s', (identity,))


def debit(conn, window, charges):
    if (not isinstance(window, str) or not window or not charges or set(charges) - METRICS
            or any(not _amount(value) for value in charges.values())):
        raise BudgetUnavailable('Resource allowance is not configured.')
    try:
        conn.execute('SELECT research.budget_debit(%s,%s)', (window, Jsonb(charges)))
    except psycopg.errors.RaiseException:
        raise BudgetUnavailable('The reviewed resource allowance is unavailable or exhausted. Retry after operator review.') from None


def reserve_search(conn, window, run_id, generation, attempts):
    search_liability(attempts)
    try:
        conn.execute('SELECT research.budget_reserve_search(%s,%s,%s,%s)', (window, run_id, generation, attempts))
    except psycopg.errors.RaiseException:
        raise BudgetUnavailable('New searches have reached the reviewed resource allowance. Existing work remains saved.') from None


def authorize_launch(conn, window, run_id, generation, attempt):
    try:
        conn.execute('SELECT research.budget_authorize_launch(%s,%s,%s,%s)', (window, run_id, generation, attempt))
    except psycopg.errors.RaiseException:
        raise BudgetUnavailable('This launch has no current reserved resource allowance. Accepted work remains queued.') from None


def reserve_migration(conn, window, generation, retained_object_bytes):
    if (not isinstance(generation, str) or not re.fullmatch('[0-9a-f]{32}', generation)
            or type(retained_object_bytes) is not int or not 1 <= retained_object_bytes <= FINAL_OBJECT_BYTES):
        raise BudgetUnavailable('Invalid migration storage allowance.')
    try:
        conn.execute('SELECT research.budget_reserve_migration(%s,%s,%s)',
                     (window, generation, retained_object_bytes))
    except psycopg.errors.RaiseException:
        raise BudgetUnavailable('Migration storage allowance is unavailable, exhausted, or conflicts with its prior receipt.') from None


def _call(conn, function, *arguments):
    try:
        statement = psycopg.sql.SQL('SELECT research.{}({}) AS value').format(
            psycopg.sql.Identifier(function), psycopg.sql.SQL(',').join(psycopg.sql.Placeholder() for _ in arguments))
        row = conn.execute(statement, arguments).fetchone()
        return row['value'] if isinstance(row, dict) else row[0]
    except psycopg.errors.RaiseException:
        raise BudgetUnavailable('Persistent storage receipt is unavailable, exhausted, or conflicts with its lifecycle.') from None


def retire_run(conn, window, run_id):
    """Call inside the GC transaction after all generation deletions and proofs."""
    if not isinstance(run_id, str) or not run_id:
        raise BudgetUnavailable('Invalid run storage identity.')
    return _call(conn, 'budget_retire_run', window, run_id)


class Budget:
    """Pool must be independent of caller transactions, including read snapshots.

    ``connection`` may instead be an injected context-manager factory. Its exit
    must commit successfully before the protected cloud call is made.
    """
    def __init__(self, pool, window, *, connection=None):
        self.connection = connection or pool.connection
        self.window = window

    def charge(self, **charges):
        with self.connection() as conn:
            debit(conn, self.window, charges)

    def _object_call(self, operation, identity, *arguments):
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
            raise BudgetUnavailable('Invalid physical object identity.')
        with self.connection() as conn:
            return _call(conn, 'budget_' + operation, self.window, identity, *arguments)

    def prepare_object(self, identity, size, sha256):
        """Commit one physical reservation; only 'new' permits a creation request."""
        if (type(size) is not int or not 0 <= size <= MAX_PHYSICAL_OBJECT_BYTES
                or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256)):
            raise BudgetUnavailable('Invalid physical object receipt.')
        return self._object_call('prepare_object', identity, size, sha256)

    def get_object_receipt(self, identity):
        return self._object_call('get_object_receipt', identity)

    def verify_object(self, identity):
        """Caller must first verify complete object size and SHA-256."""
        self._object_call('verify_object', identity)

    def delete_object(self, identity):
        """Caller must first DELETE a verified object and prove HEAD returns404."""
        return self._object_call('delete_object', identity)

    def retire_run(self, run_id):
        """Autonomous variant; never call while holding a run or generation lock."""
        with self.connection() as conn:
            return retire_run(conn, self.window, run_id)

    def result_charge(self, kind, *, bytes=0):
        if not _amount(bytes):
            raise BudgetUnavailable('Invalid object operation allowance.')
        if kind in ('put', 'copy', 'list'):
            charges = {'r2_class_a': 1}
        elif kind in ('get', 'range', 'head'):
            charges = {'r2_class_b': 1}
        elif kind == 'delete':
            charges = {}
        else:
            raise BudgetUnavailable('Unrecognized object operation.')
        charges['google_egress_bytes'] = REQUEST_OVERHEAD_BYTES + (bytes if kind == 'put' else 0)
        self.charge(**charges)

    def response(self, size):
        if not _amount(size):
            raise BudgetUnavailable('Invalid response allowance.')
        self.charge(api_response_bytes=size, google_egress_bytes=size + REQUEST_OVERHEAD_BYTES)

    def invocation(self, *, operator=False):
        self.charge(**{'operator_executions' if operator else 'service_invocations': 1})
