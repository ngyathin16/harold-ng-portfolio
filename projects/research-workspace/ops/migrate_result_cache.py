"""Offline, resumable conversion of PostgreSQL result caches to private objects.

The default CLI only plans. Applying requires independently reviewed, recent
control-plane evidence, a paused target, and a direct session-affine connection.
The source transaction is always repeatable-read and read-only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import backup, result_recovery
from service.result_builder import PackBuilder
from service.result_codec import FrozenDictionary, MAX_RESULTS, RECORD_BYTES
from service.result_objects import ObjectRef, ResultObjects
from service.result_store import MAX_ATTEMPT_BYTES, MAX_ATTEMPT_OBJECTS, ORDERS, ResultReader, SEGMENT_ROWS


class MigrationBlocked(ValueError):
    pass


class UncertainUpload(MigrationBlocked):
    """A charged PUT may still complete; an absent object does not permit retry."""


_PUBLICATION_FIELDS = ('id', 'dataset_id', 'snapshot_id', 'source_sha256', 'bundle_sha256',
                       'schema_fingerprint', 'scope', 'manifest')
_TERMINAL = {'complete', 'cancelled', 'failed', 'resource_limited'}


def _packed(value):
    def scalar(item):
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        raise TypeError('Unsupported migration metadata value')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False, default=scalar).encode('utf-8')


def _sha(value):
    return hashlib.sha256(_packed(value)).hexdigest()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON object key')
        value[key] = item
    return value


def _identity(connection):
    return dict(host=connection.info.host, port=connection.info.port, dbname=connection.info.dbname)


def _server_identity(connection):
    return connection.execute('SELECT current_database() AS dbname,inet_server_addr()::text AS address,inet_server_port() AS port').fetchone()


def _columns(connection, table):
    rows = connection.execute('''SELECT a.attname,a.atttypid FROM pg_attribute a
        WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''',
                              ('research.' + table,)).fetchall()
    return {row['attname']: row['atttypid'] for row in rows}


def _compatible(source, target, table):
    source_columns = _columns(source, table)
    if target is not None:
        target_columns = _columns(target, table)
        if any(target_columns.get(name) != kind for name, kind in source_columns.items()):
            raise MigrationBlocked('Source and target metadata schemas differ: ' + table)
    return source_columns


def _quiescent(target, verify_quiescence):
    if not callable(verify_quiescence) or verify_quiescence() is not True:
        raise MigrationBlocked('Current control-plane quiescence verification is required')
    row = target.execute('SELECT paused FROM research.dispatch_control WHERE singleton').fetchone()
    if row is None or row['paused'] is not True:
        raise MigrationBlocked('Target dispatcher must remain paused')
    if target.execute("SELECT 1 FROM research.query_runs WHERE state='running' OR lease_owner IS NOT NULL OR lease_until IS NOT NULL LIMIT 1").fetchone():
        raise MigrationBlocked('Target has an active run or lease')
    if target.execute("SELECT 1 FROM research.execution_reservations WHERE state NOT IN ('terminal','rejected') LIMIT 1").fetchone():
        raise MigrationBlocked('Target has outstanding execution reservations')


@contextmanager
def _operator_lock(target):
    acquired = target.execute('SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired', (backup.LOCK_NAME,)).fetchone()['acquired']
    if not acquired:
        raise MigrationBlocked('Another backup, retention, import or cleanup is running')
    try:
        yield
    finally:
        target.execute('SELECT pg_advisory_unlock(hashtext(%s))', (backup.LOCK_NAME,))


def _metadata_matches(target, run, subscriptions, *, required=False):
    current = target.execute('SELECT * FROM research.query_runs WHERE id=%s', (run['id'],)).fetchone()
    if current is None:
        if required:
            raise MigrationBlocked('Staged target run disappeared')
        if target.execute('SELECT 1 FROM research.query_runs WHERE query_hash=%s', (run['query_hash'],)).fetchone():
            raise MigrationBlocked('Target query identity belongs to a different run ID')
    elif _packed({name: current[name] for name in run}) != _packed(run):
        raise MigrationBlocked('Existing target run metadata differs from source')
    existing = target.execute('SELECT * FROM research.query_subscriptions WHERE run_id=%s ORDER BY id', (run['id'],)).fetchall()
    by_id = {item['id']: item for item in subscriptions}
    for item in existing:
        if item['id'] not in by_id or _packed(item) != _packed(by_id[item['id']]):
            raise MigrationBlocked('Existing target subscriptions differ from source')
    if required and len(existing) != len(subscriptions):
        raise MigrationBlocked('Staged target subscriptions changed')
    for item in subscriptions:
        conflict = target.execute('SELECT * FROM research.query_subscriptions WHERE id=%s OR (run_id=%s AND session_hash=%s)',
                                  (item['id'], item['run_id'], item['session_hash'])).fetchall()
        if any(_packed(row) != _packed(item) for row in conflict):
            raise MigrationBlocked('Target subscription identity conflicts with source')
    if target.execute('SELECT 1 FROM research.query_artifacts WHERE run_id=%s LIMIT 1', (run['id'],)).fetchone():
        raise MigrationBlocked('Query artifacts require a separate artifact migration')
    if target.execute('SELECT 1 FROM research.query_results WHERE run_id=%s LIMIT 1', (run['id'],)).fetchone():
        raise MigrationBlocked('Target already contains legacy result rows')
    return current


def _rules(source, target, run):
    publication = source.execute('SELECT * FROM research.publications WHERE id=%s', (run['publication_id'],)).fetchone()
    if publication is None or publication['status'] != 'published':
        raise MigrationBlocked('Source publication must be published')
    row = source.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND version=%s',
                         (run['publication_id'], run['ruleset_version'])).fetchone()
    if row is None:
        raise MigrationBlocked('Source frozen ruleset is missing')
    if target is not None:
        other_publication = target.execute('SELECT * FROM research.publications WHERE id=%s', (run['publication_id'],)).fetchone()
        other = target.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND version=%s',
                               (run['publication_id'], run['ruleset_version'])).fetchone()
        if (other_publication is None or other_publication['status'] != 'published' or other is None
                or _packed(row['rules']) != _packed(other['rules'])
                or _packed({key: publication[key] for key in _PUBLICATION_FIELDS}) !=
                _packed({key: other_publication[key] for key in _PUBLICATION_FIELDS})):
            raise MigrationBlocked('Target published dataset or frozen ruleset differs from source')
    return row['rules']


def _logical_row(run_id, discovery, value):
    return dict(run_id=run_id, configuration_key=value['configuration_key'],
                champion_set_key=value['champion_set_key'], active_trait_count=value['active_trait_count'],
                total_cost=value['total_cost'], champion_names=value['names_sort'],
                discovery_index=discovery, result=value)


def _build_rows(source, run, dictionary, builder):
    with source.cursor(name='migrate_results_' + uuid.uuid4().hex) as cursor:
        cursor.itersize = 256
        cursor.execute('SELECT ' + ','.join(result_recovery.FIELDS) +
                       ' FROM research.query_results WHERE run_id=%s ORDER BY discovery_index,configuration_key', (run['id'],))
        while batch := cursor.fetchmany(256):
            start = 0
            while start < len(batch):
                end = start + 1
                while end < len(batch) and batch[end]['discovery_index'] == batch[end - 1]['discovery_index'] + 1:
                    end += 1
                selected = batch[start:end]
                records = builder.append([row['result'] for row in selected], selected[0]['discovery_index'])
                if len(records) != len(selected):
                    raise MigrationBlocked('Source result identities are duplicated')
                for row, record in zip(selected, records):
                    discovery, value = dictionary.decode(record)
                    if _packed(row) != _packed(_logical_row(run['id'], discovery, value)):
                        raise MigrationBlocked('Source result metadata and decoded payload differ')
                    yield row
                start = end


def _insert(connection, table, values, columns):
    names = list(values)
    parameters = [Jsonb(values[name]) if columns[name] in (114, 3802) else values[name] for name in names]
    connection.execute(sql.SQL('INSERT INTO research.{} ({}) VALUES ({})').format(
        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, names)),
        sql.SQL(',').join(sql.Placeholder() for _ in names)), parameters)


def _stage(target, run, subscriptions, run_columns, subscription_columns, fingerprint, verify_quiescence):
    generation = hashlib.sha256(b'research-result-migration-v1\0' + run['id'].encode() + b'\0' +
                                fingerprint['sha256'].encode()).hexdigest()[:32]
    _quiescent(target, verify_quiescence)
    with target.transaction():
        target.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('research-results:' + run['id'],))
        current = _metadata_matches(target, run, subscriptions)
        if current is None:
            _insert(target, 'query_runs', run, run_columns)
        for item in subscriptions:
            if not target.execute('SELECT 1 FROM research.query_subscriptions WHERE id=%s', (item['id'],)).fetchone():
                _insert(target, 'query_subscriptions', item, subscription_columns)
        existing = target.execute('SELECT id,source_fingerprint FROM research.result_generations WHERE run_id=%s', (run['id'],)).fetchall()
        if existing:
            if len(existing) != 1 or existing[0]['id'] != generation or _packed(existing[0]['source_fingerprint']) != _packed(fingerprint):
                raise MigrationBlocked('Retry source fingerprint differs from the staged migration')
        else:
            if current is not None and current['result_generation_id'] is not None:
                raise MigrationBlocked('Target has a different published result generation')
            target.execute('INSERT INTO research.result_generations(id,run_id,source_fingerprint) VALUES(%s,%s,%s)',
                           (generation, run['id'], Jsonb(fingerprint)))
        if current is not None and current['result_generation_id'] not in (None, generation):
            raise MigrationBlocked('Target has a different published result generation')
    return generation


def _upload(target, objects, generation, name, source, verify_quiescence):
    if isinstance(source, Path):
        size, digest = source.stat().st_size, backup.digest_file(source)
    else:
        size, digest = len(source), hashlib.sha256(source).hexdigest()
    expected = ObjectRef(generation + '/' + name, size, digest)
    _quiescent(target, verify_quiescence)
    with target.transaction():
        existing = target.execute('SELECT * FROM research.result_object_intents WHERE key=%s FOR UPDATE', (expected.key,)).fetchone()
        if existing is not None:
            if (existing['generation_id'] != generation or existing['size'] != size or existing['sha256'] != digest
                    or existing['state'] not in ('planned', 'verified')):
                raise MigrationBlocked('Existing object intent differs from the migration source')
        else:
            used = target.execute('SELECT count(*) AS n,COALESCE(sum(size),0) AS bytes FROM research.result_object_intents WHERE generation_id=%s',
                                  (generation,)).fetchone()
            if used['n'] >= MAX_ATTEMPT_OBJECTS or used['bytes'] + size > MAX_ATTEMPT_BYTES:
                raise MigrationBlocked('Migration object reservation exceeds the generation allowance')
            target.execute('INSERT INTO research.result_object_intents(generation_id,key,size,sha256) VALUES(%s,%s,%s,%s)',
                           (generation, expected.key, size, digest))
    if existing is not None:
        if not objects.exists(expected.key):
            if existing['state'] == 'planned':
                raise UncertainUpload('A previous charged PUT is uncertain; absence does not authorize another PUT')
            raise MigrationBlocked('A previously verified object is missing')
        objects.verify(expected)
    else:
        target.execute('SELECT 1')
        actual = objects.put(expected.key, source)
        if actual != expected:
            raise MigrationBlocked('Uploaded object differs from its committed intent')
    _quiescent(target, verify_quiescence)
    with target.transaction():
        target.execute("UPDATE research.result_object_intents SET state='verified' WHERE key=%s", (expected.key,))
    return expected


def _publish(target, objects, run, subscriptions, generation, dictionary, builder, checked, verify_quiescence, scratch):
    dictionary_ref = _upload(target, objects, generation, 'dictionary.json', dictionary.to_bytes(), verify_quiescence)
    orders, tail, segments = None, b'', []
    if run['state'] == 'complete':
        orders = {}
        for order in ORDERS:
            pack = builder.build(order)
            ref = _upload(target, objects, generation, order + '.bin', pack.path, verify_quiescence)
            orders[order] = dict(object=ref.to_dict(), count=pack.count, frame_sha256=list(pack.frame_sha256))
    else:
        frame = bytearray()
        start = 0
        for record in builder.iter_records('discovery'):
            frame.extend(record)
            if len(frame) == SEGMENT_ROWS * RECORD_BYTES:
                ref = _upload(target, objects, generation, 'segment-' + str(start) + '.bin', bytes(frame), verify_quiescence)
                segments.append((start, ref))
                start += SEGMENT_ROWS
                frame.clear()
        tail = bytes(frame)
    _quiescent(target, verify_quiescence)
    with target.transaction():
        _metadata_matches(target, run, subscriptions, required=True)
        for start, ref in segments:
            previous = target.execute('SELECT object FROM research.result_segments WHERE generation_id=%s AND first_row=%s', (generation, start)).fetchone()
            if previous is not None and _packed(previous['object']) != _packed(ref.to_dict()):
                raise MigrationBlocked('Existing migration segment differs')
            if previous is None:
                target.execute('INSERT INTO research.result_segments(generation_id,first_row,object) VALUES(%s,%s,%s)',
                               (generation, start, Jsonb(ref.to_dict())))
        target.execute('UPDATE research.result_generations SET dictionary=%s,visible_count=%s,tail=%s,complete_orders=%s WHERE id=%s',
                       (Jsonb(dictionary_ref.to_dict()), builder.count, tail, Jsonb(orders) if orders is not None else None, generation))
    state = dict(run, result_generation_id=generation, result_dictionary=dictionary_ref.to_dict(),
                 result_tail=tail, visible_count=builder.count, complete_orders=orders)
    actual = result_recovery.logical_check(ResultReader(objects).iter_rows(target, state), scratch)
    if actual != checked:
        raise MigrationBlocked('Uploaded logical result checksum differs from source')
    _quiescent(target, verify_quiescence)
    with target.transaction():
        target.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('research-results:' + run['id'],))
        _metadata_matches(target, run, subscriptions, required=True)
        if target.execute("SELECT 1 FROM research.result_object_intents WHERE generation_id=%s AND state<>'verified' LIMIT 1", (generation,)).fetchone():
            raise MigrationBlocked('Unverified object intent prevents publication')
        expected_keys = {dictionary_ref.key} | ({value['object']['key'] for value in orders.values()} if orders is not None
                                               else {ref.key for _, ref in segments})
        actual_keys = {row['key'] for row in target.execute('SELECT key FROM research.result_object_intents WHERE generation_id=%s', (generation,))}
        if actual_keys != expected_keys:
            raise MigrationBlocked('Migration object inventory differs from the source')
        target.execute('UPDATE research.query_runs SET result_generation_id=%s WHERE id=%s', (generation, run['id']))


def migrate(source, target=None, objects=None, *, scratch, verify_quiescence=None, execute=False, run_ids=None,
            budget_window=None):
    """Plan locally or publish verified generations without changing source metadata.

    Connections use dict_row and must be idle; the target must use autocommit.
    Object requests use the supplied ResultObjects.charge callback. The caller
    must provide live control-plane checks, or the CLI's reviewed evidence file.
    """
    if source.info.transaction_status != TransactionStatus.IDLE:
        raise MigrationBlocked('Source connection must be idle before its read-only snapshot')
    if target is not None and (target.info.transaction_status != TransactionStatus.IDLE or not target.autocommit):
        raise MigrationBlocked('Target requires an idle autocommit connection')
    if target is not None and _identity(source) == _identity(target):
        raise MigrationBlocked('Source and target databases must differ')
    if type(execute) is not bool or execute and (target is None or objects is None):
        raise MigrationBlocked('Execution requires a separate target and private object storage')
    if execute and (not isinstance(budget_window, str) or not budget_window):
        raise MigrationBlocked('Execution requires a reviewed persistent storage budget window')
    if run_ids is not None and (type(run_ids) not in (tuple, list) or not run_ids or any(type(value) is not str for value in run_ids)):
        raise MigrationBlocked('Run selection must be a nonempty list of identities')
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    report = {'mode': 'execute' if execute else 'plan', 'source_modified': False, 'queries': []}

    def perform():
        if execute:
            _quiescent(target, verify_quiescence)
            if 'source_fingerprint' not in _columns(target, 'result_generations'):
                raise MigrationBlocked('Target result migration schema is missing')
        with source.transaction():
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            source.execute("SET LOCAL TIME ZONE 'UTC'")
            collation = source.execute('SELECT datcollate,datlocprovider FROM pg_database WHERE datname=current_database()').fetchone()
            if collation['datlocprovider'] != 'c' or collation['datcollate'] not in ('C', 'POSIX'):
                raise MigrationBlocked('Source champion-name collation has not been qualified for byte ordering')
            if target is not None and _server_identity(source) == _server_identity(target):
                raise MigrationBlocked('Source and target resolve to the same database')
            run_columns = _compatible(source, target, 'query_runs')
            subscription_columns = _compatible(source, target, 'query_subscriptions')
            parameters = (list(run_ids),) if run_ids is not None else ()
            rows = source.execute('SELECT * FROM research.query_runs' + (' WHERE id=ANY(%s)' if run_ids is not None else '') + ' ORDER BY id', parameters).fetchall()
            if run_ids is not None and {row['id'] for row in rows} != set(run_ids):
                raise MigrationBlocked('Selected source run is missing')
            for source_run in rows:
                if source_run.get('result_generation_id') is not None:
                    raise MigrationBlocked('Source must contain authoritative legacy PostgreSQL results')
                run = {key: value for key, value in source_run.items() if key != 'result_generation_id'}
                if run['state'] not in _TERMINAL or run['lease_owner'] is not None or run['lease_until'] is not None:
                    raise MigrationBlocked('Source runs must be terminal and unleased')
                if type(run['result_count']) is not int or not 0 <= run['result_count'] <= MAX_RESULTS:
                    raise MigrationBlocked('Source result count exceeds the codec allowance')
                if source.execute('SELECT 1 FROM research.query_artifacts WHERE run_id=%s LIMIT 1', (run['id'],)).fetchone():
                    raise MigrationBlocked('Query artifacts require a separate artifact migration')
                subscriptions = source.execute('SELECT * FROM research.query_subscriptions WHERE run_id=%s ORDER BY id', (run['id'],)).fetchall()
                rules = _rules(source, target, run)
                if target is not None:
                    _metadata_matches(target, run, subscriptions)
                dictionary = FrozenDictionary.from_rules(rules)
                with PackBuilder(dictionary, scratch) as builder:
                    checked = result_recovery.logical_check(_build_rows(source, run, dictionary, builder), scratch)
                    if checked['stored_rows'] != run['result_count'] or checked['stored_champion_sets'] != builder.champion_set_count:
                        raise MigrationBlocked('Source result counts differ from logical rows')
                    if run['champion_set_count'] is not None and checked['stored_champion_sets'] != run['champion_set_count']:
                        raise MigrationBlocked('Source champion-set count differs from logical rows')
                    details = {'version': 1, 'run_sha256': _sha(run), 'subscriptions_sha256': _sha(subscriptions),
                               'rules_sha256': _sha(rules), 'logical_check_version': result_recovery.CHECK_VERSION,
                               'logical_check': checked}
                    fingerprint = dict(details, sha256=_sha(details))
                    item = dict(id=run['id'], source_fingerprint=fingerprint['sha256'], **checked)
                    if run['state'] == 'complete':
                        packs = [builder.build(order) for order in ORDERS]
                        item['object_count'] = 4
                        item['object_bytes'] = len(dictionary.to_bytes()) + sum(pack.count * RECORD_BYTES for pack in packs)
                    else:
                        item['object_count'] = 1 + builder.count // SEGMENT_ROWS
                        item['object_bytes'] = len(dictionary.to_bytes()) + builder.count // SEGMENT_ROWS * SEGMENT_ROWS * RECORD_BYTES
                    if execute:
                        generation = _stage(target, run, subscriptions, run_columns, subscription_columns, fingerprint, verify_quiescence)
                        from service.budget import reserve_migration
                        with target.transaction():
                            reserve_migration(target, budget_window, generation, item['object_bytes'])
                        _publish(target, objects, run, subscriptions, generation, dictionary, builder, checked, verify_quiescence, scratch)
                        item['generation_id'] = generation
                    report['queries'].append(item)
        return report

    if execute:
        with _operator_lock(target):
            return perform()
    return perform()


def quiescence_report(path, target_identity, expected_resources, *, environment_id=None, now=None):
    """Read evidence anew at every guard, including after a long local build."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=_unique_object)
        checked = datetime.fromisoformat(value['checked_at'].replace('Z', '+00:00'))
        current = now or datetime.now(timezone.utc)
        if checked.tzinfo is None or not 0 <= (current - checked).total_seconds() <= 300:
            raise ValueError
        if value['target'] != target_identity:
            raise ValueError
        checks = value['checks']
        for field in ('api_writers_disabled', 'dispatcher_disabled', 'maintenance_writers_disabled'):
            if checks[field] is not True:
                raise ValueError
        for field in ('outstanding_worker_executions', 'outstanding_maintenance_executions'):
            if type(checks[field]) is not int or checks[field] != 0:
                raise ValueError
        resources = value['resources']
        if not isinstance(resources, dict) or any(type(resources.get(field)) is not str or not resources[field]
                                                 for field in ('project', 'api_service', 'dispatcher_service', 'worker_job', 'maintenance_job')):
            raise ValueError
        if resources != expected_resources or value.get('environment_id') != environment_id:
            raise ValueError
        if value.get('environment_id') is not None and (type(value['environment_id']) is not str or not value['environment_id']):
            raise ValueError
        return True
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        raise MigrationBlocked('Quiescence report is missing, stale, or does not cover this target') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--scratch', required=True)
    parser.add_argument('--quiescence-report')
    parser.add_argument('--run-id', action='append')
    args = parser.parse_args()
    source_url = backup.require_env('RESEARCH_MIGRATION_SOURCE_DATABASE_URL')
    target_url = os.environ.get('RESEARCH_MIGRATION_TARGET_DATABASE_URL')
    if args.execute and (not target_url or not args.quiescence_report):
        parser.error('--execute requires a target database and --quiescence-report')
    from psycopg.conninfo import conninfo_to_dict
    from deploy.managed import require_tls
    for url in (source_url, target_url):
        if url and conninfo_to_dict(url).get('host') not in ('localhost', '127.0.0.1', '::1'):
            require_tls(url, session=True)
    from contextlib import ExitStack
    with ExitStack() as stack:
        source = stack.enter_context(psycopg.connect(source_url, row_factory=dict_row))
        target = stack.enter_context(psycopg.connect(target_url, row_factory=dict_row, autocommit=True)) if target_url else None
        expected_resources = json.loads(backup.require_env('RESEARCH_MIGRATION_EXPECTED_RESOURCES_JSON'),
                                        object_pairs_hook=_unique_object) if args.execute else None
        verifier = (lambda: quiescence_report(args.quiescence_report, _identity(target), expected_resources,
                    environment_id=os.environ.get('RESEARCH_MIGRATION_ENVIRONMENT_ID'))) if args.execute else None
        objects = None
        window = None
        if args.execute:
            from psycopg_pool import ConnectionPool
            from service.budget import Budget
            from service.config import Settings
            window = backup.require_env('RESEARCH_BUDGET_WINDOW_ID')
            _quiescent(target, verifier)
            budget_pool = stack.enter_context(ConnectionPool(target_url, min_size=0, max_size=1,
                                                             kwargs={'row_factory': dict_row}))
            budget = Budget(budget_pool, window)
            budget.invocation(operator=True)
            objects = ResultObjects.from_settings(Settings.from_env(), charge=budget.result_charge)
            stack.callback(objects.close)
        print(json.dumps(migrate(source, target, objects, scratch=args.scratch, verify_quiescence=verifier,
                                 execute=args.execute, run_ids=args.run_id, budget_window=window)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        from service.budget import BudgetUnavailable
        report = {'status': 'failed', 'category': type(error).__name__}
        if isinstance(error, (MigrationBlocked, BudgetUnavailable)):
            report['reason'] = str(error)
        print(json.dumps(report))
        raise SystemExit(1)
