"""Logical result verification and recovery of immutable result generations."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import heapq
import io
from pathlib import Path
import struct
import tempfile
import uuid


CHECK_VERSION = 2
MAX_ROWS = 1_000_000
DIGEST_SCRATCH_BYTES = 128 * 1024 * 1024
HASH_CHUNK_ROWS = 65_536
FIELDS = ('run_id', 'configuration_key', 'champion_set_key', 'active_trait_count',
          'total_cost', 'champion_names', 'discovery_index', 'result')


def has_generations(connection):
    return connection.execute("SELECT to_regclass('research.result_generations') AS name").fetchone()['name'] is not None


def configured_objects(*, charge=None):
    from service.config import Settings
    from service.result_objects import ResultObjects
    settings = Settings.from_env()
    if settings.result_storage_mode == 'objects' and settings.deployment_mode == 'serverless' and charge is None:
        raise ValueError('Cloud result recovery requires a committed resource allowance callback')
    return ResultObjects.from_settings(settings, charge=charge)


def staged_objects(directory):
    from service.config import Settings
    from service.result_objects import ResultObjects
    return ResultObjects.from_settings(Settings(result_storage_mode='objects',
        result_local_root=str(directory), result_prefix='results/'))


def inventory(connection):
    if not has_generations(connection):
        return []
    from service.result_store import inventory as result_inventory
    return result_inventory(connection)


def _field(digest, name, value):
    from .backup import packed
    key, data = name.encode('utf-8'), packed(value)
    digest.update(struct.pack('>I', len(key)))
    digest.update(key)
    digest.update(struct.pack('>Q', len(data)))
    digest.update(data)


def row_digest(row):
    digest = hashlib.sha256(b'research-query-row-v2\0')
    for name in FIELDS:
        _field(digest, name, row[name])
    return digest.digest()


def _hashes(stream):
    while value := stream.read(32):
        if len(value) != 32:
            raise ValueError('Truncated logical result digest spool')
        yield value


def _sorted_hashes(source, directory, label):
    chunks = []
    with source.open('rb') as stream:
        while data := stream.read(32 * HASH_CHUNK_ROWS):
            if len(data) % 32:
                raise ValueError('Truncated logical result digest spool')
            values = sorted(data[offset:offset + 32] for offset in range(0, len(data), 32))
            path = directory / (label + '-' + str(len(chunks)))
            path.write_bytes(b''.join(values))
            chunks.append(path)
    try:
        with ExitStack() as stack:
            streams = [stack.enter_context(path.open('rb')) for path in chunks]
            yield from heapq.merge(*(_hashes(stream) for stream in streams))
    finally:
        for path in chunks:
            path.unlink()
        source.unlink()


def logical_check(rows, scratch=None):
    """V2 is a sorted multiset of framed row hashes, not the V1 SQL-order hash."""
    with tempfile.TemporaryDirectory(prefix='result-digests-', dir=scratch) as temporary:
        directory = Path(temporary)
        row_path, champion_path = directory / 'rows', directory / 'champions'
        count = 0
        with row_path.open('xb') as row_stream, champion_path.open('xb') as champion_stream:
            for row in rows:
                count += 1
                if count > MAX_ROWS:
                    raise ValueError('Logical result recount exceeds its row allowance')
                row_stream.write(row_digest(row))
                champion = hashlib.sha256(b'research-champion-set-v2\0')
                _field(champion, 'champion_set_key', row['champion_set_key'])
                champion_stream.write(champion.digest())
        digest = hashlib.sha256(b'research-query-multiset-v2\0' + struct.pack('>Q', count))
        for value in _sorted_hashes(row_path, directory, 'sorted-rows'):
            digest.update(struct.pack('>I', len(value)))
            digest.update(value)
        champions, previous = 0, None
        for value in _sorted_hashes(champion_path, directory, 'sorted-champions'):
            if value != previous:
                champions += 1
                previous = value
        return dict(stored_rows=count, stored_champion_sets=champions, rows_sha256=digest.hexdigest())


def logical_rows(connection, run, objects=None):
    if run.get('result_generation_id') is not None:
        from service.result_store import ResultReader, shared_lock
        shared_lock(connection, run['id'])
        state = connection.execute('''SELECT r.*,g.dictionary AS result_dictionary,g.tail AS result_tail,
            g.visible_count,g.complete_orders FROM research.query_runs r
            LEFT JOIN research.result_generations g ON g.id=r.result_generation_id WHERE r.id=%s''',
            (run['id'],)).fetchone()
        if state is None or state['visible_count'] != state['result_count']:
            raise ValueError('Result generation visibility differs from its run')
        if not state['result_count']:
            return
        yield from ResultReader(objects or configured_objects()).iter_rows(connection, state)
        return
    with connection.cursor(name='logical_results_' + uuid.uuid4().hex) as cursor:
        cursor.itersize = 1000
        cursor.execute('SELECT ' + ','.join(FIELDS) + ' FROM research.query_results WHERE run_id=%s', (run['id'],))
        yield from cursor


def query_checks(connection, objects=None, scratch=None):
    generation = ',result_generation_id' if has_generations(connection) else ''
    runs = connection.execute('SELECT id,state,result_count,champion_set_count,expires_at>now() AS retained' +
                              generation + ' FROM research.query_runs ORDER BY id').fetchall()
    checks = []
    for run in runs:
        checked = logical_check(logical_rows(connection, run, objects), scratch)
        if run.get('result_generation_id') is not None and checked['stored_rows'] != run['result_count']:
            raise ValueError('Logical result count differs from visible object records')
        if run['state'] == 'complete' and run['retained'] and (
                checked['stored_rows'] != run['result_count'] or checked['stored_champion_sets'] != run['champion_set_count']):
            raise ValueError('Retained complete result counts differ from logical records')
        checks.append({key: run[key] for key in ('id', 'state', 'result_count', 'champion_set_count')} | checked)
    return checks


class SourceStore:
    """Adapt bounded result reads to existing checked backup staging and copies."""
    def __init__(self, objects, refs, connection=None):
        self.objects, self.refs = objects, {ref.key: ref for ref in refs}
        self.connection = connection

    def check(self):
        if self.connection is not None:
            self.connection.execute('SELECT 1')

    def read(self, name):
        self.check()
        ref = self.refs[name]
        return io.BytesIO(self.objects.get(ref, max_bytes=ref.size))

    def read_for_copy(self, name):
        from .backup import CopySource
        self.check()
        ref = self.refs[name]
        etag = self.objects.verify(ref)
        if etag is None:
            raise ValueError('Server-side result backup requires an R2 source')
        stream = self.read(name)
        return stream, CopySource(self.objects.endpoint, self.objects.bucket, self.objects.key(name), etag)


def restore_members(objects, manifest, directory, *, archive_store=None, copy_store=None):
    from . import backup
    from service.result_objects import ObjectRef
    restored = 0
    for item in manifest['members']:
        if not item['path'].startswith('results/'):
            continue
        ref = ObjectRef(item['path'][8:], item['size'], item['sha256'])
        if copy_store is not None:
            source = backup.check_stream(archive_store, manifest['backup_id'] + '/' + item['path'], item, for_copy=True)
            copy_store.copy(ref.key, source)
            backup.check_stream(copy_store, ref.key, item)
        else:
            actual = objects.put(ref.key, Path(directory) / item['path'])
            if actual != ref:
                raise ValueError('Restored result object differs from its backup')
            objects.verify(ref)
        restored += 1
    return restored


def cleanup(connection, objects, *, run_ids=None, budget=None):
    """Caller holds the backup lock; newly verified upload intents need a second cleanup pass."""
    if not has_generations(connection):
        return {'deleted_result_generations': 0, 'deleted_result_objects': 0, 'deferred_result_generations': 0}
    from service.result_objects import ObjectRef
    has_reservations = connection.execute("SELECT to_regclass('research.execution_reservations') AS name").fetchone()['name'] is not None
    candidates = 'SELECT run_id FROM research.result_generations'
    if budget is not None:
        candidates += """ UNION SELECT id AS run_id FROM research.query_runs
            WHERE state IN ('complete','cancelled','failed','resource_limited') AND expires_at<=now()"""
    runs = connection.execute('SELECT DISTINCT run_id FROM (' + candidates + ') AS candidates' +
        (' WHERE run_id=ANY(%s)' if run_ids is not None else '') + ' ORDER BY run_id',
        (list(run_ids),) if run_ids is not None else ()).fetchall()
    deleted_generations = deleted_objects = deferred_generations = 0
    for item in runs:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('research-results:' + item['run_id'],))
            run = connection.execute('''SELECT id,state,lease_owner,result_generation_id,expires_at<=now() AS expired
                FROM research.query_runs WHERE id=%s FOR UPDATE''', (item['run_id'],)).fetchone()
            if run is None or run['state'] in ('queued', 'running') or run['lease_owner'] is not None:
                continue
            if has_reservations and connection.execute('''SELECT 1 FROM research.execution_reservations
                    WHERE run_id=%s AND state NOT IN ('terminal','rejected') LIMIT 1''', (run['id'],)).fetchone():
                continue
            generations = connection.execute('SELECT * FROM research.result_generations WHERE run_id=%s ORDER BY id', (run['id'],)).fetchall()
            for generation in generations:
                current = generation['id'] == run['result_generation_id']
                if current and not run['expired']:
                    continue
                values = [generation['dictionary']] if generation['dictionary'] else []
                if generation['complete_orders']:
                    values.extend(pack['object'] for pack in generation['complete_orders'].values())
                values.extend(row['object'] for row in connection.execute('SELECT object FROM research.result_segments WHERE generation_id=%s', (generation['id'],)))
                intents = connection.execute('SELECT key,size,sha256,state FROM research.result_object_intents WHERE generation_id=%s',
                                             (generation['id'],)).fetchall()
                values.extend(dict(key=row['key'], size=row['size'], sha256=row['sha256']) for row in intents)
                refs = {}
                for value in values:
                    ref = ObjectRef.from_dict(value)
                    if not ref.key.startswith(generation['id'] + '/') or ref.key in refs and refs[ref.key] != ref:
                        raise ValueError('Result cleanup reference leaves its generation or conflicts')
                    refs[ref.key] = ref
                unresolved = False
                for intent in intents:
                    if intent['state'] != 'planned':
                        continue
                    # Commit completion proof before any irreversible deletion. The
                    # caller may own an outer transaction, so defer this generation.
                    unresolved = True
                    connection.execute('SELECT 1')
                    if not objects.exists(intent['key']):
                        break
                    objects.verify(refs[intent['key']])
                    connection.execute("UPDATE research.result_object_intents SET state='verified' WHERE key=%s",
                                       (intent['key'],))
                if unresolved:
                    deferred_generations += 1
                    continue
                for ref in refs.values():
                    connection.execute('SELECT 1')
                    objects.delete(ref)
                if current:
                    connection.execute('UPDATE research.query_runs SET result_generation_id=NULL WHERE id=%s', (run['id'],))
                connection.execute('DELETE FROM research.result_generations WHERE id=%s', (generation['id'],))
                deleted_generations += 1
                deleted_objects += len(refs)
            if budget is not None:
                from service.budget import retire_run
                retire_run(connection, budget.window, run['id'])
    return {'deleted_result_generations': deleted_generations, 'deleted_result_objects': deleted_objects,
            'deferred_result_generations': deferred_generations}
