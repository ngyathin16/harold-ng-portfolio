"""Durable wakeup hints. PostgreSQL alone decides what work may execute."""
import base64
from dataclasses import dataclass
import re
import uuid
from urllib.parse import quote

import httpx

from .cloud import access_token as metadata_token
from .dispatch import OUTSTANDING


class MarkerUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Marker:
    name: str
    object_generation: str
    run_id: str
    generation: int


def lock_key(run_id, generation):
    return 'research-dispatch-marker:' + str(generation) + ':' + run_id


def prepare(conn, markers, run_id, generation):
    if conn.autocommit:
        raise ValueError('Dispatch markers require an acceptance transaction')
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (lock_key(run_id, generation),))
    try:
        markers.create(run_id, generation)
    except Exception:
        raise MarkerUnavailable('Search wakeup storage is unavailable. Retry shortly.') from None


class GCSMarkers:
    def __init__(self, settings, client=None, access_token=None):
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]', settings.dispatch_marker_bucket):
            raise ValueError('Configure a private dispatch marker bucket')
        if not re.fullmatch(r'[A-Za-z0-9/_-]{1,120}', settings.dispatch_marker_prefix):
            raise ValueError('Invalid dispatch marker prefix')
        self.prefix = settings.dispatch_marker_prefix.rstrip('/') + '/'
        self.objects_url = 'https://storage.googleapis.com/storage/v1/b/' + settings.dispatch_marker_bucket + '/o'
        self.upload_url = 'https://storage.googleapis.com/upload/storage/v1/b/' + settings.dispatch_marker_bucket + '/o'
        self.client = client or httpx.Client(timeout=3, trust_env=False)
        self.access_token = access_token

    def request(self, method, url, **kwargs):
        return self.client.request(method, url,
            headers={'Authorization': 'Bearer ' + (self.access_token or metadata_token(self.client)),
                     **kwargs.pop('headers', {})}, **kwargs)

    def parse(self, item):
        name = item['name']
        if not name.startswith(self.prefix):
            raise ValueError('Unexpected marker prefix')
        parts = name[len(self.prefix):].split('/')
        if len(parts) != 3 or not re.fullmatch(r'[A-Za-z0-9_-]{1,216}', parts[0]):
            raise ValueError('Invalid dispatch marker name')
        if not re.fullmatch(r'[1-9][0-9]{0,18}', parts[1]) or not re.fullmatch(r'[a-f0-9]{32}', parts[2]):
            raise ValueError('Invalid dispatch marker generation')
        run_id = base64.urlsafe_b64decode(parts[0] + '=' * (-len(parts[0]) % 4)).decode('utf-8')
        generation = str(item['generation'])
        if not run_id or len(run_id) > 160 or not re.fullmatch(r'[0-9]+', generation):
            raise ValueError('Invalid dispatch marker identity')
        return Marker(name, generation, run_id, int(parts[1]))

    def create(self, run_id, generation):
        encoded = base64.urlsafe_b64encode(run_id.encode()).decode().rstrip('=')
        name = self.prefix + encoded + '/' + str(generation) + '/' + uuid.uuid4().hex
        response = self.request('POST', self.upload_url,
            params={'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'},
            headers={'Content-Type': 'application/octet-stream'}, content=b'')
        response.raise_for_status()
        marker = self.parse(response.json())
        if marker.name != name:
            raise ValueError('Marker creation was not confirmed')
        return marker

    def list(self):
        response = self.request('GET', self.objects_url,
            params={'prefix': self.prefix, 'maxResults': 256, 'fields': 'items(name,generation),nextPageToken'})
        response.raise_for_status()
        page = response.json()
        items = page.get('items', [])
        if not items and page.get('nextPageToken'):
            raise MarkerUnavailable('Marker listing is incomplete; retry shortly.')
        return [self.parse(item) for item in items]

    def delete(self, marker):
        response = self.request('DELETE', self.objects_url + '/' + quote(marker.name, safe=''),
            params={'ifGenerationMatch': marker.object_generation})
        if response.status_code not in (404, 412):
            response.raise_for_status()

    def close(self):
        self.client.close()


def cleanup(pool, markers, listed):
    deleted = 0
    for marker in listed:
        if deleted >= 16:
            break
        with pool.connection() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            locked = conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked',
                (lock_key(marker.run_id, marker.generation),)).fetchone()['locked']
            if not locked:
                continue
            # This statement must take a fresh snapshot after acquiring the lock.
            needed = conn.execute("""SELECT
                EXISTS(SELECT 1 FROM research.query_runs WHERE id=%s AND dispatch_generation=%s
                       AND state IN ('queued','running')) OR
                EXISTS(SELECT 1 FROM research.execution_reservations WHERE run_id=%s AND generation=%s
                       AND state=ANY(%s)) AS needed""",
                (marker.run_id, marker.generation, marker.run_id, marker.generation, list(OUTSTANDING))).fetchone()['needed']
            if not needed:
                markers.delete(marker)
                deleted += 1
    return deleted


def repair(conn, markers):
    """Backfill a new environment after restore, before enabling admission."""
    if conn.autocommit:
        raise ValueError('Marker repair requires a paused transaction')
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('research-query-admission'))")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('research-dispatch-admission'))")
    if not conn.execute('SELECT paused FROM research.dispatch_control WHERE singleton').fetchone()['paused']:
        raise ValueError('Pause dispatch before repairing markers')
    rows = conn.execute("""SELECT id AS run_id,dispatch_generation AS generation
        FROM research.query_runs WHERE state IN ('queued','running')
        UNION SELECT run_id,generation FROM research.execution_reservations WHERE state=ANY(%s)""",
        (list(OUTSTANDING),)).fetchall()
    for row in rows:
        if row['generation'] < 1:
            raise ValueError('Queued work must have a dispatch generation before marker repair')
        prepare(conn, markers, row['run_id'], row['generation'])
    return len(rows)
