"""Stream completed query exports into verified, immutable private objects."""
from contextlib import contextmanager
import csv
from datetime import date, datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit

import httpx


class StaleGeneration(ValueError):
    """The execution changed while its export was being prepared."""


def _json(value):
    def convert(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError('Unsupported export value')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False, default=convert)


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def generation_hash(run):
    fields = ('id', 'query_hash', 'solver_version', 'ruleset_version', 'attempts',
              'started_at', 'finished_at', 'result_count', 'champion_set_count')
    return hashlib.sha256(_json({key: run.get(key) for key in fields}).encode()).hexdigest()


class _RemoteStorage:
    """Bounded file transfer; credentials stay on the configured HTTPS host."""
    def __init__(self, settings):
        parsed = urlsplit(settings.supabase_url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Object storage requires a clean HTTPS project URL')
        self.base = settings.supabase_url.rstrip('/') + '/storage/v1/object/' + quote(settings.storage_bucket, safe='') + '/'
        self.key = settings.supabase_key

    def put_verified(self, key, path, expected, media_type):
        url = self.base + quote(key, safe='/')
        headers = {'Authorization': 'Bearer ' + self.key, 'apikey': self.key,
                   'Content-Type': media_type, 'Content-Length': str(path.stat().st_size), 'x-upsert': 'false'}
        try:
            with httpx.Client(follow_redirects=False, timeout=120) as client:
                with path.open('rb') as source:
                    response = client.post(url, headers=headers, content=iter(lambda: source.read(1024 * 1024), b''))
                if response.status_code not in (200, 201, 400, 409):
                    raise ValueError('Private export upload failed (HTTP ' + str(response.status_code) + ')')
                with client.stream('GET', url, headers={'Authorization': headers['Authorization'], 'apikey': self.key}) as response:
                    if response.status_code != 200:
                        raise ValueError('Private export verification failed (HTTP ' + str(response.status_code) + ')')
                    digest = hashlib.sha256()
                    for chunk in response.iter_raw():
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError('Private export checksum mismatch')
        except httpx.HTTPError:
            raise ValueError('Private export storage request failed; retry artifact preparation') from None

    def delete(self, key):
        endpoint = self.base.rstrip('/')
        headers = {'Authorization': 'Bearer ' + self.key, 'apikey': self.key}
        try:
            with httpx.Client(follow_redirects=False, timeout=120) as client:
                response = client.request('DELETE', endpoint, headers=headers, json={'prefixes': [key]})
                if response.status_code not in (200, 204, 404):
                    raise ValueError('Private export deletion failed (HTTP ' + str(response.status_code) + ')')
        except httpx.HTTPError:
            raise ValueError('Private export deletion request failed; cleanup can be retried') from None


def _storage(settings):
    if settings.artifact_root:
        from data.import_snapshot import LocalStorage
        return LocalStorage(settings.artifact_root)
    if settings.supabase_url and settings.supabase_key:
        return _RemoteStorage(settings)
    return None


class _HashedWriter(io.RawIOBase):
    def __init__(self, target):
        self.target = target
        self.digest = hashlib.sha256()

    def writable(self):
        return True

    def write(self, value):
        self.digest.update(value)
        return self.target.write(value)

    def flush(self):
        if not self.closed:
            self.target.flush()


@contextmanager
def _compressed_text(path, restored_filename):
    with path.open('wb') as output:
        with gzip.GzipFile(filename=restored_filename, fileobj=output, mode='wb', mtime=0, compresslevel=6) as compressed:
            hashed = _HashedWriter(compressed)
            with io.TextIOWrapper(hashed, encoding='utf-8', newline='') as stream:
                yield stream, hashed.digest


def _csv_value(value):
    if value is None:
        return '\\N'
    return value.replace('\\', '\\\\') if isinstance(value, str) else value


def _encode(conn, run, rules, directory):
    generation = generation_hash(run)
    provenance = {'query_id': run['id'], 'publication_id': run['publication_id'], 'query': run['definition'],
                  'ruleset_version': run['ruleset_version'], 'ruleset': rules, 'solver_version': run['solver_version'],
                  'query_hash': run['query_hash'], 'generation_hash': generation, 'state': 'complete', 'complete': True,
                  'configuration_count': run['result_count'], 'champion_set_count': run['champion_set_count'],
                  'completeness_scope': 'All matches under the recorded rules and assumptions',
                  'started_at': run['started_at'], 'finished_at': run['finished_at'],
                  'csv_encoding': 'One provenance row followed by configuration rows. Exact \\N is null; unescape doubled backslashes after null decoding.'}
    paths = {kind: directory / ('query-' + run['id'] + '.' + kind + '.gz') for kind in ('json', 'csv')}
    restored = {kind: 'query-' + run['id'] + '.' + kind for kind in paths}
    fields = ['record_type', 'configuration_key', 'champion_count', 'board_slots', 'total_cost',
              'active_trait_count', 'members_json', 'traits_json', 'unknown_traits_json', 'metadata_json']
    count = 0
    with _compressed_text(paths['json'], restored['json']) as (js, json_digest), \
         _compressed_text(paths['csv'], restored['csv']) as (cs, csv_digest):
        js.write('{"provenance":' + _json(provenance) + ',"results":[')
        writer = csv.writer(cs)
        writer.writerow(fields)
        writer.writerow(['provenance'] + [''] * 8 + [_csv_value(_json(provenance))])
        with conn.cursor(name='query_artifact_rows') as cursor:
            cursor.itersize = 500
            cursor.execute('''SELECT result FROM research.query_results WHERE run_id=%s
                ORDER BY active_trait_count DESC,total_cost NULLS LAST,champion_names,configuration_key''', (run['id'],))
            for row in cursor:
                result = row['result']
                if count:
                    js.write(',')
                js.write(_json(result))
                values = ['configuration'] + [result.get(key) for key in ('configuration_key', 'champion_count', 'board_slots',
                                                                         'total_cost', 'active_trait_count')]
                values += [_json(result.get(key)) for key in ('members', 'traits', 'unknown_traits')] + ['']
                writer.writerow([_csv_value(value) for value in values])
                count += 1
        js.write(']}\n')
        if count != run['result_count']:
            raise ValueError('Completed result count differs from exported rows; artifacts were not published')
    artifacts = []
    for kind, restored_digest in [('json', json_digest), ('csv', csv_digest)]:
        path = paths[kind]
        checksum = _sha(path)
        artifacts.append({'run_id': run['id'], 'format': kind, 'generation_hash': generation,
                          'storage_key': 'queries/' + run['id'] + '/' + checksum + '.' + kind + '.gz',
                          'size': path.stat().st_size, 'sha256': checksum, 'media_type': 'application/gzip',
                          'restored_filename': restored[kind], 'restored_sha256': restored_digest.hexdigest(), 'path': path})
    return artifacts


def materialize(pool, settings, run_id):
    """Publish both verified formats only if their completed generation still exists."""
    store = _storage(settings)
    if store is None:
        return []
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', run_id):
        raise ValueError('Invalid query identity for artifact storage')
    with tempfile.TemporaryDirectory(prefix='research-query-exports-') as temporary:
        with pool.connection() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            run = conn.execute('''SELECT r.*,p.status AS publication_status,r.expires_at>now() AS retained
                FROM research.query_runs r JOIN research.publications p ON p.id=r.publication_id WHERE r.id=%s''', (run_id,)).fetchone()
            if run is None or run['state'] != 'complete' or run['publication_status'] != 'published' or not run['retained']:
                raise ValueError('Only retained, published, complete runs can prepare export artifacts')
            rules = conn.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND version=%s',
                                 (run['publication_id'], run['ruleset_version'])).fetchone()
            if rules is None:
                raise ValueError('The completed run has no retained ruleset')
            artifacts = _encode(conn, run, rules['rules'], Path(temporary))
        with pool.connection() as conn:
            for artifact in artifacts:
                conn.execute('''INSERT INTO research.query_artifact_gc(storage_key,delete_after)
                    VALUES (%s,now()+interval '1 day') ON CONFLICT(storage_key) DO UPDATE
                    SET delete_after=GREATEST(research.query_artifact_gc.delete_after,EXCLUDED.delete_after)''',
                    (artifact['storage_key'],))
        for artifact in artifacts:
            store.put_verified(artifact['storage_key'], artifact['path'], artifact['sha256'], artifact['media_type'])
        with pool.connection() as conn:
            current = conn.execute('''SELECT r.*,p.status AS publication_status,r.expires_at>now() AS retained
                FROM research.query_runs r JOIN research.publications p ON p.id=r.publication_id WHERE r.id=%s FOR UPDATE OF r''', (run_id,)).fetchone()
            if current is None or current['state'] != 'complete' or current['publication_status'] != 'published' or \
               not current['retained'] or generation_hash(current) != artifacts[0]['generation_hash']:
                raise StaleGeneration('Query execution changed while exports were prepared; old objects remain private')
            old = conn.execute('SELECT storage_key FROM research.query_artifacts WHERE run_id=%s', (run_id,)).fetchall()
            for artifact in artifacts:
                conn.execute('''INSERT INTO research.query_artifacts
                    (run_id,format,storage_key,size,sha256,media_type,generation_hash,restored_filename,restored_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(run_id,format) DO UPDATE SET storage_key=EXCLUDED.storage_key,size=EXCLUDED.size,
                    sha256=EXCLUDED.sha256,media_type=EXCLUDED.media_type,generation_hash=EXCLUDED.generation_hash,
                    restored_filename=EXCLUDED.restored_filename,restored_sha256=EXCLUDED.restored_sha256''',
                    tuple(artifact[key] for key in ('run_id', 'format', 'storage_key', 'size', 'sha256', 'media_type',
                                                   'generation_hash', 'restored_filename', 'restored_sha256')))
                conn.execute('DELETE FROM research.query_artifact_gc WHERE storage_key=%s', (artifact['storage_key'],))
            retained = {artifact['storage_key'] for artifact in artifacts}
            for item in old:
                if item['storage_key'] not in retained:
                    conn.execute('''INSERT INTO research.query_artifact_gc(storage_key,delete_after)
                        VALUES (%s,now()+interval '2 minutes') ON CONFLICT(storage_key) DO NOTHING''', (item['storage_key'],))
        return [{key: value for key, value in artifact.items() if key != 'path'} for artifact in artifacts]


def delete_expired(pool, settings):
    """Delete only expired query objects, retaining failed cleanup records for retry."""
    store = _storage(settings)
    if store is None:
        return 0
    with pool.connection() as conn:
        candidates = conn.execute('''SELECT a.storage_key FROM research.query_artifacts a JOIN research.query_runs r ON r.id=a.run_id
            WHERE r.expires_at<=now() UNION SELECT g.storage_key FROM research.query_artifact_gc g WHERE g.delete_after<=now()
            AND NOT EXISTS (SELECT 1 FROM research.query_artifacts a JOIN research.query_runs r ON r.id=a.run_id
                WHERE a.storage_key=g.storage_key AND (r.expires_at IS NULL OR r.expires_at>now()))''').fetchall()
    deleted = 0
    for item in candidates:
        key = item['storage_key']
        if not key.startswith('queries/') or '..' in key.split('/'):
            raise ValueError('Query cleanup cannot delete source-publication artifacts')
        if settings.artifact_root:
            root = Path(settings.artifact_root).resolve()
            path = (root / key).resolve()
            if not path.is_relative_to(root / 'queries'):
                raise ValueError('Query cleanup path leaves its storage namespace')
            path.unlink(missing_ok=True)
        else:
            store.delete(key)
        with pool.connection() as conn:
            conn.execute('''DELETE FROM research.query_artifacts a USING research.query_runs r
                WHERE a.run_id=r.id AND a.storage_key=%s AND r.expires_at<=now()''', (key,))
            conn.execute('DELETE FROM research.query_artifact_gc WHERE storage_key=%s', (key,))
        deleted += 1
    return deleted
