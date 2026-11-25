"""Bounded, disposable SQLite indexes for immutable result packs."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterable, Iterator, Literal

from .result_codec import FrozenDictionary, MAX_DISCOVERY_INDEX, MAX_RESULTS, RECORD_BYTES


FRAME_RECORDS = 4096
ResultOrder = Literal['active_traits', 'total_cost', 'champions', 'discovery']
_PAGE_BYTES = 4096
_SCRATCH_MARGIN = 64 * 1024
_FETCH_ROWS = 256
_ORDERS = {
    'discovery': 'SELECT record FROM records ORDER BY ordinal',
    'active_traits': ('SELECT r.record FROM active_order o CROSS JOIN records r ON r.ordinal=o.ordinal '
                      'ORDER BY o.active DESC,o.key'),
    'total_cost': ('SELECT r.record FROM cost_order o CROSS JOIN records r ON r.ordinal=o.ordinal '
                   'ORDER BY o.is_null,o.cost,o.key'),
    'champions': ('SELECT r.record FROM names_order o CROSS JOIN records r ON r.ordinal=o.ordinal '
                  'ORDER BY o.names,o.key'),
}


class ScratchLimitExceeded(ValueError):
    """The disposable builder cannot fit within its reserved local storage."""


@dataclass(frozen=True, slots=True)
class BuiltPack:
    path: Path
    count: int
    sha256: str
    frame_sha256: tuple[str, ...]


class PackBuilder:
    """Owns a private child directory. Close removes only files it created.

    Every ordering is a pre-existing B-tree, avoiding SQLite sort spill files.
    A page ceiling reserves space for all four output packs. The rollback journal
    stays in memory and cannot exceed the database ceiling plus page metadata;
    callers should append modest batches to keep its typical footprint small.
    The first build seals the input. Returned pack paths live until close.
    """

    def __init__(self, dictionary: FrozenDictionary, directory: str | Path,
                 max_results: int = MAX_RESULTS, max_scratch_bytes: int = 512 * 1024 * 1024):
        if not isinstance(dictionary, FrozenDictionary):
            raise TypeError('dictionary must be a FrozenDictionary')
        if type(max_results) is not int or not 1 <= max_results <= MAX_RESULTS:
            raise ValueError('max_results must be between 1 and 1000000')
        if type(max_scratch_bytes) is not int or max_scratch_bytes <= 0:
            raise ValueError('max_scratch_bytes must be positive')
        database_budget = max_scratch_bytes - 4 * max_results * RECORD_BYTES - _SCRATCH_MARGIN
        if database_budget < 16 * _PAGE_BYTES:
            raise ScratchLimitExceeded('Scratch budget cannot reserve the database and output packs')
        parent = Path(directory)
        parent.mkdir(parents=True, exist_ok=True)
        self._directory = Path(tempfile.mkdtemp(prefix='result-pack-', dir=parent))
        self._paths: set[Path] = set()
        self._dictionary = dictionary
        self._max_results = max_results
        self._max_scratch_bytes = max_scratch_bytes
        self._closed = False
        self._sealed = False
        self._count = 0
        self._champion_count = 0
        self._packs: dict[str, BuiltPack] = {}
        self._connection: sqlite3.Connection | None = None
        database = self._directory / 'indexes.sqlite'
        self._paths.add(database)
        try:
            self._connection = sqlite3.connect(database, isolation_level=None)
            connection = self._connection
            connection.execute(f'PRAGMA page_size={_PAGE_BYTES}')
            connection.execute(f'PRAGMA max_page_count={database_budget // _PAGE_BYTES}')
            connection.execute('PRAGMA journal_mode=MEMORY')
            connection.execute('PRAGMA synchronous=OFF')
            connection.execute('PRAGMA cache_size=-16384')
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('PRAGMA mmap_size=0')
            connection.executescript('''
                CREATE TABLE records(ordinal INTEGER PRIMARY KEY,key BLOB NOT NULL UNIQUE,record BLOB NOT NULL);
                CREATE TABLE champion_sets(key BLOB PRIMARY KEY) WITHOUT ROWID;
                CREATE TABLE active_order(active INTEGER,key BLOB,ordinal INTEGER NOT NULL,
                                          PRIMARY KEY(active DESC,key)) WITHOUT ROWID;
                CREATE TABLE cost_order(is_null INTEGER,cost INTEGER,key BLOB,ordinal INTEGER NOT NULL,
                                        PRIMARY KEY(is_null,cost,key)) WITHOUT ROWID;
                CREATE TABLE names_order(names BLOB,key BLOB,ordinal INTEGER NOT NULL,
                                         PRIMARY KEY(names,key)) WITHOUT ROWID;
            ''')
            self._check_scratch()
        except BaseException:
            self.close()
            raise

    @property
    def count(self) -> int:
        return self._count

    @property
    def champion_set_count(self) -> int:
        return self._champion_count

    def _open(self) -> sqlite3.Connection:
        if self._closed or self._connection is None:
            raise RuntimeError('Pack builder is closed')
        return self._connection

    def _check_scratch(self, additional_bytes: int = 0) -> None:
        size = sum(path.stat().st_size for path in self._paths if path.exists())
        if size + additional_bytes > self._max_scratch_bytes:
            raise ScratchLimitExceeded('Result builder scratch limit exceeded')

    def append(self, payloads: Iterable[dict], discovery_start: int) -> list[bytes]:
        connection = self._open()
        if self._sealed:
            raise RuntimeError('Cannot append after building a pack')
        if type(discovery_start) is not int or not 1 <= discovery_start <= MAX_DISCOVERY_INDEX:
            raise ValueError('Discovery start must be a positive signed PostgreSQL bigint')
        self._check_scratch()
        accepted = []
        champion_count = self._champion_count
        connection.execute('BEGIN IMMEDIATE')
        try:
            for offset, payload in enumerate(payloads):
                ordinal = discovery_start + offset
                record = self._dictionary.encode(payload, ordinal)
                key = bytes.fromhex(payload['configuration_key'])
                if connection.execute('SELECT 1 FROM records WHERE key=?', (key,)).fetchone():
                    continue
                if self._count + len(accepted) >= self._max_results:
                    raise ValueError('Result count exceeds the reserved row limit')
                cost = payload['total_cost']
                if cost is not None and not -(1 << 63) <= cost <= MAX_DISCOVERY_INDEX:
                    raise ValueError('Result cost exceeds the SQLite integer range')
                connection.execute('INSERT INTO records VALUES(?,?,?)', (ordinal, key, record))
                inserted = connection.execute('INSERT OR IGNORE INTO champion_sets VALUES(?)',
                                              (bytes.fromhex(payload['champion_set_key']),)).rowcount
                champion_count += inserted
                connection.execute('INSERT INTO active_order VALUES(?,?,?)', (payload['active_trait_count'], key, ordinal))
                connection.execute('INSERT INTO cost_order VALUES(?,?,?,?)', (cost is None, cost or 0, key, ordinal))
                connection.execute('INSERT INTO names_order VALUES(?,?,?)', (payload['names_sort'].encode('utf-8'), key, ordinal))
                accepted.append(record)
                if len(accepted) % _FETCH_ROWS == 0:
                    self._check_scratch()
            self._check_scratch()
            connection.execute('COMMIT')
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            if isinstance(exc, sqlite3.OperationalError) and getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
                raise ScratchLimitExceeded('SQLite reached the reserved scratch limit') from exc
            if isinstance(exc, sqlite3.IntegrityError):
                raise ValueError('Distinct results must have distinct discovery ordinals') from exc
            raise
        self._count += len(accepted)
        self._champion_count = champion_count
        return accepted

    def iter_records(self, order: ResultOrder) -> Iterator[bytes]:
        connection = self._open()
        if order not in _ORDERS:
            raise ValueError('Unsupported result order')
        cursor = connection.execute(_ORDERS[order])
        try:
            while batch := cursor.fetchmany(_FETCH_ROWS):
                for (record,) in batch:
                    yield record
        finally:
            cursor.close()

    def build(self, order: ResultOrder) -> BuiltPack:
        self._open()
        if order not in _ORDERS:
            raise ValueError('Unsupported result order')
        self._sealed = True
        if order in self._packs:
            return self._packs[order]
        self._check_scratch(self._count * RECORD_BYTES)
        path = self._directory / (order + '.pack')
        digest = hashlib.sha256()
        frames = []
        count = 0
        frame = bytearray()
        try:
            with path.open('xb') as stream:
                self._paths.add(path)
                for record in self.iter_records(order):
                    frame.extend(record)
                    count += 1
                    if count % FRAME_RECORDS == 0:
                        stream.write(frame)
                        digest.update(frame)
                        frames.append(hashlib.sha256(frame).hexdigest())
                        frame.clear()
                        self._check_scratch()
                if frame:
                    stream.write(frame)
                    digest.update(frame)
                    frames.append(hashlib.sha256(frame).hexdigest())
            if count != self._count or path.stat().st_size != count * RECORD_BYTES:
                raise ValueError('Result pack count differs from the builder')
            self._check_scratch()
        except BaseException:
            if path in self._paths:
                path.unlink(missing_ok=True)
                self._paths.discard(path)
            raise
        result = BuiltPack(path=path, count=count, sha256=digest.hexdigest(), frame_sha256=tuple(frames))
        self._packs[order] = result
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        for path in self._paths:
            path.unlink(missing_ok=True)
        self._paths.clear()
        if self._directory.exists() and not any(self._directory.iterdir()):
            self._directory.rmdir()

    def __enter__(self) -> PackBuilder:
        self._open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
