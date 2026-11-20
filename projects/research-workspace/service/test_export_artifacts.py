"""Real PostgreSQL + private local object storage export lifecycle checks."""
import csv
from dataclasses import replace
import gzip
import hashlib
import io
import json
from pathlib import Path

import pytest
import httpx

from service import export_artifacts
from service.config import Settings
from service.test_service import service, submission
from service.worker import claim, execute


def completed(service, **query):
    client, pool, settings, publication, rules = service
    posted = submission(client, publication, **query)
    run = claim(pool, settings)
    assert run['id'] == posted['query_id']
    execute(pool, settings, run)
    assert client.get('/v1/queries/' + run['id']).json()['complete']
    return run['id']


def test_compressed_exports_preserve_provenance_counts_hashes_and_retry(service, tmp_path):
    _, pool, settings, _, rules = service
    identity = completed(service)
    settings = replace(settings, artifact_root=str(tmp_path))
    artifacts = export_artifacts.materialize(pool, settings, identity)
    assert len(artifacts) == 2
    assert export_artifacts.materialize(pool, settings, identity) == artifacts
    for artifact in artifacts:
        compressed = (tmp_path / artifact['storage_key']).read_bytes()
        assert len(compressed) == artifact['size']
        assert hashlib.sha256(compressed).hexdigest() == artifact['sha256']
        restored = gzip.decompress(compressed)
        assert hashlib.sha256(restored).hexdigest() == artifact['restored_sha256']
        if artifact['format'] == 'json':
            payload = json.loads(restored)
            assert payload['provenance']['ruleset'] == rules
            assert payload['provenance']['configuration_count'] == len(payload['results']) == 3
            assert payload['provenance']['complete'] is True
        else:
            rows = list(csv.DictReader(io.StringIO(restored.decode())))
            assert rows[0]['record_type'] == 'provenance'
            assert len(rows[1:]) == 3
            metadata = json.loads(rows[0]['metadata_json'].replace('\\\\', '\\'))
            assert metadata['ruleset'] == rules
            assert metadata['configuration_count'] == 3
    with pool.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research.query_artifacts WHERE run_id=%s', (identity,)).fetchone()['n'] == 2


def test_generation_change_during_upload_never_publishes_old_export(service, tmp_path, monkeypatch):
    _, pool, settings, _, _ = service
    identity = completed(service)
    settings = replace(settings, artifact_root=str(tmp_path))
    store = export_artifacts._storage(settings)
    original = store.put_verified
    mutated = False
    def upload(*args):
        nonlocal mutated
        original(*args)
        if not mutated:
            mutated = True
            with pool.connection() as conn:
                conn.execute("UPDATE research.query_runs SET state='queued',started_at=NULL,finished_at=NULL WHERE id=%s", (identity,))
    monkeypatch.setattr(store, 'put_verified', upload)
    monkeypatch.setattr(export_artifacts, '_storage', lambda unused: store)
    with pytest.raises(export_artifacts.StaleGeneration):
        export_artifacts.materialize(pool, settings, identity)
    with pool.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research.query_artifacts WHERE run_id=%s', (identity,)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM research.query_artifact_gc WHERE storage_key LIKE %s', ('queries/' + identity + '/%',)).fetchone()['n'] == 2
        conn.execute('DELETE FROM research.query_artifact_gc WHERE storage_key LIKE %s', ('queries/' + identity + '/%',))


def test_missing_rows_and_unconfigured_storage_are_explicit(service, tmp_path):
    _, pool, settings, _, _ = service
    assert export_artifacts.materialize(None, settings, 'unused') == []
    identity = completed(service)
    settings = replace(settings, artifact_root=str(tmp_path))
    with pool.connection() as conn:
        conn.execute('DELETE FROM research.query_results WHERE run_id=%s AND configuration_key=(SELECT min(configuration_key) FROM research.query_results WHERE run_id=%s)', (identity, identity))
    with pytest.raises(ValueError, match='count differs'):
        export_artifacts.materialize(pool, settings, identity)
    assert list(tmp_path.rglob('*.gz')) == []


def test_empty_results_keep_rules_and_retryable_cleanup_preserves_source_files(service, tmp_path, monkeypatch):
    _, pool, settings, _, _ = service
    identity = completed(service, total_cost={'max': 0})
    settings = replace(settings, artifact_root=str(tmp_path))
    artifacts = export_artifacts.materialize(pool, settings, identity)
    artifact = next(a for a in artifacts if a['format'] == 'json')
    payload = json.loads(gzip.decompress((tmp_path / artifact['storage_key']).read_bytes()))
    assert payload['results'] == [] and payload['provenance']['configuration_count'] == 0
    source = tmp_path / 'source-snapshot.json'
    source.write_text('retained source artifact')
    assert export_artifacts.delete_expired(pool, settings) == 0
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET expires_at=now()-interval '1 second' WHERE id=%s", (identity,))
    original = Path.unlink
    def fail(path, *args, **kwargs):
        if path.suffix == '.gz':
            raise PermissionError('synthetic storage failure')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail)
        with pytest.raises(PermissionError):
            export_artifacts.delete_expired(pool, settings)
    with pool.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM research.query_artifacts WHERE run_id=%s', (identity,)).fetchone()['n'] == 2
    assert export_artifacts.delete_expired(pool, settings) == 2
    assert source.read_text() == 'retained source artifact'
    assert list(tmp_path.rglob('*.gz')) == []


def test_remote_transfer_streams_chunks_and_verifies_stored_bytes(tmp_path, monkeypatch):
    source = tmp_path / 'export.gz'
    source.write_bytes(b'x' * (3 * 1024 * 1024 + 7))
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    chunk_sizes = []
    class Download(httpx.SyncByteStream):
        def __iter__(self):
            with source.open('rb') as stream:
                while chunk := stream.read(256 * 1024):
                    yield chunk
    def handler(request):
        assert request.url.host == 'project.supabase.co'
        assert request.headers['apikey'] == 'synthetic-storage-test'
        if request.method == 'POST':
            digest = hashlib.sha256()
            for chunk in request.stream:
                chunk_sizes.append(len(chunk))
                digest.update(chunk)
            assert digest.hexdigest() == expected
            return httpx.Response(201)
        return httpx.Response(200, stream=Download())
    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            return handler(request)
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=Transport(), **kwargs))
    store = export_artifacts._RemoteStorage(Settings(supabase_url='https://project.supabase.co', supabase_key='synthetic-storage-test'))
    store.put_verified('queries/run/file.json.gz', source, expected, 'application/gzip')
    assert sum(chunk_sizes) == source.stat().st_size
    assert max(chunk_sizes) <= 1024 * 1024


def test_remote_redirect_never_forwards_credentials(tmp_path, monkeypatch):
    source = tmp_path / 'export.gz'
    source.write_bytes(b'result')
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(307, headers={'Location': 'https://third-party.invalid/collect'})
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    store = export_artifacts._RemoteStorage(Settings(supabase_url='https://project.supabase.co', supabase_key='synthetic-storage-test'))
    with pytest.raises(ValueError, match='HTTP 307') as failure:
        store.put_verified('queries/run/file.json.gz', source, hashlib.sha256(b'result').hexdigest(), 'application/gzip')
    assert 'synthetic-storage-test' not in str(failure.value)
    assert len(requests) == 1 and requests[0].startswith('https://project.supabase.co/')
