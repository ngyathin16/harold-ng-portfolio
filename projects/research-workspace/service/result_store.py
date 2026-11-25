"""Fenced result publication; PostgreSQL owns visibility of immutable objects."""
from __future__ import annotations

import hashlib
from pathlib import Path
import secrets
import tempfile
import re

from psycopg.types.json import Jsonb

from .config import CapacityUnavailable
from .result_builder import PackBuilder, FRAME_RECORDS
from .result_codec import FrozenDictionary, RECORD_BYTES
from .result_objects import ObjectRef, ResultObjects

SEGMENT_ROWS = FRAME_RECORDS
MAX_ATTEMPT_BYTES = 112 * 1024 * 1024
MAX_ATTEMPT_OBJECTS = 1000000 // SEGMENT_ROWS + 4
ORDERS = ('active_traits', 'total_cost', 'champions')


class ResultLeaseLost(Exception):
    pass


def shared_lock(conn, run_id):
    conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))", ('research-results:' + run_id,))


def snapshot(conn, run_id):
    return conn.execute('''SELECT r.*,g.dictionary AS result_dictionary,g.tail AS result_tail,
        g.visible_count,g.complete_orders FROM research.query_runs r
        JOIN research.publications p ON p.id=r.publication_id
        LEFT JOIN research.result_generations g ON g.id=r.result_generation_id
        WHERE r.id=%s AND p.status='published' ''', (run_id,)).fetchone()


def inventory(conn):
    refs = {}
    rows = conn.execute('''SELECT g.* FROM research.result_generations g
        JOIN research.query_runs r ON r.result_generation_id=g.id ORDER BY g.id''').fetchall()
    for row in rows:
        values = [row['dictionary']] if row['dictionary'] else []
        if row['complete_orders'] is not None:
            values.extend(pack['object'] for pack in row['complete_orders'].values())
        else:
            values.extend(item['object'] for item in conn.execute(
                'SELECT object FROM research.result_segments WHERE generation_id=%s ORDER BY first_row', (row['id'],)))
        for value in values:
            ref = ObjectRef.from_dict(value)
            if ref.key in refs and ref != refs[ref.key]:
                raise ValueError('Conflicting immutable result references')
            refs[ref.key] = ref
    return list(refs.values())


class ResultReader:
    def __init__(self, objects):
        self.objects = objects

    def dictionary(self, state):
        if not state['result_dictionary']:
            raise ValueError('Result dictionary is missing')
        return FrozenDictionary.from_bytes(self.objects.get(
            ObjectRef.from_dict(state['result_dictionary']), max_bytes=4 * 1024 * 1024))

    def validated_count(self, state):
        count = state.get('result_count')
        if type(count) is not int or not 0 <= count <= 1000000:
            raise ValueError('Result count is invalid')
        if state.get('result_generation_id') is None:
            if count:
                raise ValueError('Result generation has not been migrated')
            return count
        if type(state.get('visible_count')) is not int or state['visible_count'] != count:
            raise ValueError('Result count and publication disagree')
        packs = state.get('complete_orders')
        if state['state'] == 'complete':
            if type(packs) is not dict or set(packs) != set(ORDERS):
                raise ValueError('Completed result order is missing')
            for pack in packs.values():
                if type(pack) is not dict or set(pack) != {'object', 'count', 'frame_sha256'}:
                    raise ValueError('Completed result manifest is invalid')
                ref = ObjectRef.from_dict(pack['object'])
                hashes = pack['frame_sha256']
                if (type(pack['count']) is not int or pack['count'] != count or ref.size != count * RECORD_BYTES
                        or type(hashes) is not list or len(hashes) != (count + FRAME_RECORDS - 1) // FRAME_RECORDS
                        or any(type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest) for digest in hashes)):
                    raise ValueError('Completed result manifest is invalid')
        elif packs is not None:
            raise ValueError('Incomplete query exposes completed packs')
        return count

    def records(self, conn, state, order, offset, limit):
        count = self.validated_count(state)
        wanted = min(limit, max(0, count - offset))
        if not wanted:
            return
        packs = state['complete_orders']
        if state['state'] == 'complete':
            if not packs or set(packs) != set(ORDERS):
                raise ValueError('Completed result order is missing')
            pack = packs[order]
            ref = ObjectRef.from_dict(pack['object'])
            hashes = pack['frame_sha256']
            if pack['count'] != count or ref.size != count * RECORD_BYTES or len(hashes) != (count + FRAME_RECORDS - 1) // FRAME_RECORDS:
                raise ValueError('Completed result manifest is invalid')
            first, last = offset // FRAME_RECORDS, (offset + wanted - 1) // FRAME_RECORDS
            for frame in range(first, last + 1):
                start = frame * FRAME_RECORDS
                length = min(FRAME_RECORDS, count - start)
                raw = self.objects.read_range(ref, start * RECORD_BYTES, length * RECORD_BYTES)
                if hashlib.sha256(raw).hexdigest() != hashes[frame]:
                    raise ValueError('Result frame checksum differs')
                low, high = max(offset - start, 0), min(offset + wanted - start, length)
                for index in range(low, high):
                    yield raw[index * RECORD_BYTES:(index + 1) * RECORD_BYTES]
            return
        if packs is not None:
            raise ValueError('Incomplete query exposes completed packs')
        tail = bytes(state['result_tail'])
        if len(tail) % RECORD_BYTES or len(tail) >= SEGMENT_ROWS * RECORD_BYTES:
            raise ValueError('Result checkpoint tail is invalid')
        sealed = count - len(tail) // RECORD_BYTES
        if sealed < 0 or sealed % SEGMENT_ROWS:
            raise ValueError('Result checkpoint is not a dense prefix')
        first = offset // SEGMENT_ROWS * SEGMENT_ROWS
        end = offset + wanted
        for start in range(first, min(end, sealed), SEGMENT_ROWS):
            item = conn.execute('SELECT object FROM research.result_segments WHERE generation_id=%s AND first_row=%s',
                                (state['result_generation_id'], start)).fetchone()
            if item is None:
                raise ValueError('Result segment is missing')
            ref = ObjectRef.from_dict(item['object'])
            if ref.size != SEGMENT_ROWS * RECORD_BYTES:
                raise ValueError('Result segment length differs')
            raw = self.objects.get(ref, max_bytes=SEGMENT_ROWS * RECORD_BYTES)
            for index in range(max(offset - start, 0), min(end - start, SEGMENT_ROWS)):
                yield raw[index * RECORD_BYTES:(index + 1) * RECORD_BYTES]
        for index in range(max(offset - sealed, 0), max(0, min(end - sealed, len(tail) // RECORD_BYTES))):
            yield tail[index * RECORD_BYTES:(index + 1) * RECORD_BYTES]

    def page(self, conn, state, order, offset, limit):
        if order not in ORDERS or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Invalid result page')
        count = self.validated_count(state)
        if offset >= count:
            return []
        dictionary = self.dictionary(state)
        return [dictionary.decode(record)[1] for record in self.records(conn, state, order, offset, limit)]

    def iter_rows(self, conn, state):
        if not state['result_generation_id']:
            raise ValueError('Object iteration requires a result generation')
        count = self.validated_count(state)
        if not count:
            return
        dictionary = self.dictionary(state)
        for record in self.records(conn, state, 'active_traits', 0, state['result_count']):
            discovery, value = dictionary.decode(record)
            yield dict(run_id=state['id'], configuration_key=value['configuration_key'],
                       champion_set_key=value['champion_set_key'], active_trait_count=value['active_trait_count'],
                       total_cost=value['total_cost'], champion_names=value['names_sort'],
                       discovery_index=discovery, result=value)


class ResultWriter:
    def __init__(self, pool, settings, run, heartbeat, objects=None, *, budget=None):
        self.pool, self.settings, self.run, self.heartbeat = pool, settings, run, heartbeat
        self.generation = secrets.token_hex(16)
        self.dictionary = FrozenDictionary.from_rules(run['rules'])
        self.builder = PackBuilder(self.dictionary, settings.result_scratch_root or tempfile.gettempdir(),
                                   max_results=settings.max_results, max_scratch_bytes=settings.result_scratch_bytes)
        self.owns_objects = objects is None
        try:
            if settings.budget_window_id and budget is None:
                raise CapacityUnavailable('Result writes require the reviewed resource allowance')
            self.objects = objects or ResultObjects.from_settings(settings, charge=budget.result_charge if budget else None)
        except Exception:
            self.builder.close()
            raise
        self.tail = b''
        self.sealed = 0
        self.consumed = 0
        self.started = False

    def owned(self, conn, *, attached=True):
        if self.heartbeat.lost.is_set():
            raise ResultLeaseLost()
        row = conn.execute("""SELECT id,result_generation_id FROM research.query_runs
            WHERE id=%s AND lease_owner=%s AND state='running' AND lease_until>=now() FOR UPDATE""",
                           (self.run['id'], self.run['lease_owner'])).fetchone()
        if not row or attached and row['result_generation_id'] != self.generation:
            raise ResultLeaseLost()

    def start(self):
        with self.pool.connection() as conn:
            self.owned(conn, attached=False)
            self.discovery_start = conn.execute("SELECT nextval('research.result_discovery_blocks') AS n").fetchone()['n']
            conn.execute('INSERT INTO research.result_generations(id,run_id) VALUES(%s,%s)', (self.generation, self.run['id']))
            conn.execute('UPDATE research.query_runs SET result_generation_id=%s WHERE id=%s', (self.generation, self.run['id']))
        ref = self.upload('dictionary.json', self.dictionary.to_bytes())
        with self.pool.connection() as conn:
            self.owned(conn)
            conn.execute('UPDATE research.result_generations SET dictionary=%s WHERE id=%s', (Jsonb(ref.to_dict()), self.generation))
        self.started = True

    def upload(self, name, source):
        key = self.generation + '/' + name
        digest = hashlib.sha256()
        if isinstance(source, Path):
            size = source.stat().st_size
            with source.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
        else:
            size = len(source)
            digest.update(source)
        expected = ObjectRef(key, size, digest.hexdigest())
        with self.pool.connection() as conn:
            self.owned(conn)
            used = conn.execute('''SELECT count(*) AS n,COALESCE(sum(size),0) AS bytes
                FROM research.result_object_intents WHERE generation_id=%s''', (self.generation,)).fetchone()
            if used['n'] >= MAX_ATTEMPT_OBJECTS or used['bytes'] + size > MAX_ATTEMPT_BYTES:
                raise CapacityUnavailable('Result object allowance is exhausted')
            conn.execute('INSERT INTO research.result_object_intents(generation_id,key,size,sha256) VALUES(%s,%s,%s,%s)',
                         (self.generation, key, size, expected.sha256))
        actual = self.objects.put(key, source)
        if actual != expected:
            raise ValueError('Uploaded result differs from its reserved identity')
        with self.pool.connection() as conn:
            self.owned(conn)
            conn.execute("UPDATE research.result_object_intents SET state='verified' WHERE key=%s", (key,))
        return actual

    def append(self, payloads, progress):
        if not self.started:
            self.start()
        if self.consumed + len(payloads) > 1000000:
            raise CapacityUnavailable('Result discovery allowance is exhausted')
        records = self.builder.append(payloads, self.discovery_start + self.consumed)
        self.consumed += len(payloads)
        pending = self.tail + b''.join(records)
        segments = []
        size = SEGMENT_ROWS * RECORD_BYTES
        while len(pending) >= size:
            start = self.sealed + len(segments) * SEGMENT_ROWS
            ref = self.upload('segment-' + str(start) + '.bin', pending[:size])
            segments.append((start, ref))
            pending = pending[size:]
        with self.pool.connection() as conn:
            self.owned(conn)
            for start, ref in segments:
                conn.execute('INSERT INTO research.result_segments(generation_id,first_row,object) VALUES(%s,%s,%s)',
                             (self.generation, start, Jsonb(ref.to_dict())))
            conn.execute('UPDATE research.result_generations SET tail=%s,visible_count=%s WHERE id=%s',
                         (pending, self.builder.count, self.generation))
            conn.execute("""UPDATE research.query_runs SET result_count=%s,progress=%s,lease_until=now()+interval '30 seconds'
                WHERE id=%s""", (self.builder.count, Jsonb(progress), self.run['id']))
        self.sealed += len(segments) * SEGMENT_ROWS
        self.tail = pending

    def finish(self, summary):
        count, sets = self.builder.count, self.builder.champion_set_count
        if summary['complete'] and count != summary['configuration_count']:
            raise ValueError('Stored result count differs from solver validation')
        orders = None
        if summary['complete']:
            orders = {}
            for order in ORDERS:
                if self.heartbeat.lost.is_set():
                    raise ResultLeaseLost()
                pack = self.builder.build(order)
                ref = self.upload(order + '.bin', pack.path)
                orders[order] = dict(object=ref.to_dict(), count=pack.count, frame_sha256=list(pack.frame_sha256))
        with self.pool.connection() as conn:
            self.owned(conn)
            if orders is not None:
                conn.execute("UPDATE research.result_generations SET complete_orders=%s,tail='' WHERE id=%s",
                             (Jsonb(orders), self.generation))
            conn.execute("""UPDATE research.query_runs SET state=%s,progress=%s,result_count=%s,champion_set_count=%s,
                finished_at=now(),lease_owner=NULL,lease_until=NULL,error=%s,expires_at=now()+(%s*interval '1 day')
                WHERE id=%s""", (summary['state'], Jsonb(summary), count, sets, summary.get('reason'),
                                  self.settings.result_retention_days, self.run['id']))

    def close(self):
        self.builder.close()
        if self.owns_objects:
            self.objects.close()
