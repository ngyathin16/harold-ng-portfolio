"""Immutable result objects fail closed on corrupt data and unsafe paths."""
from dataclasses import FrozenInstanceError
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import traceback
from threading import Thread
from unittest.mock import Mock

import pytest

from service.result_objects import MAX_OBJECT_BYTES, MAX_RANGE_BYTES, ObjectRef, ResultObjectError, ResultObjects


def ref(key='run/pack.bin', body=b'abcdefghij'):
    return ObjectRef(key, len(body), hashlib.sha256(body).hexdigest())


class S3Error(Exception):
    def __init__(self, code, status):
        super().__init__('private endpoint or credentials must not reach logs')
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    def __init__(self):
        self.objects, self.calls, self.bodies = {}, [], []
        self.rewrite_get = None
        self.keep_deleted = False
        self.closed = False

    def put_object(self, **args):
        self.calls.append(('put', args))
        assert args['IfNoneMatch'] == '*'
        if args['Key'] in self.objects:
            raise S3Error('PreconditionFailed', 412)
        self.objects[args['Key']] = args['Body'].read()
        assert len(self.objects[args['Key']]) == args['ContentLength']
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def get_object(self, **args):
        self.calls.append(('get', args))
        if args['Key'] not in self.objects:
            raise S3Error('NoSuchKey', 404)
        value = self.objects[args['Key']]
        response = {'ResponseMetadata': {'HTTPStatusCode': 200}, 'ContentLength': len(value), 'ETag': '"strong-etag"'}
        if 'Range' in args:
            start, end = (int(item) for item in args['Range'].removeprefix('bytes=').split('-'))
            response.update(ContentRange=f'bytes {start}-{end}/{len(value)}', ContentLength=end-start+1,
                            ResponseMetadata={'HTTPStatusCode': 206})
            value = value[start:end+1]
        response['Body'] = io.BytesIO(value)
        if self.rewrite_get:
            self.rewrite_get(response)
        self.bodies.append(response['Body'])
        return response

    def delete_object(self, **args):
        self.calls.append(('delete', args))
        if not self.keep_deleted:
            self.objects.pop(args['Key'], None)
        return {'ResponseMetadata': {'HTTPStatusCode': 204}}

    def head_object(self, **args):
        self.calls.append(('head', args))
        if args['Key'] not in self.objects:
            raise S3Error('404', 404)
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def close(self):
        self.closed = True


@pytest.fixture(params=['local', 's3'])
def store(request, tmp_path):
    charges = []
    kwargs = {'local_root': tmp_path / 'objects'} if request.param == 'local' else {'client': FakeS3(), 'bucket': 'private-results'}
    value = ResultObjects(**kwargs, prefix='isolated/results/', charge=lambda kind, **kw: charges.append((kind, kw['bytes'])))
    yield value, charges
    value.close()


@pytest.mark.parametrize('key', ['', '/absolute', '../escape', 'a/../b', 'a//b', 'a/./b', 'a\\b',
                                  'C:drive', 'https://public', 'name.', 'CON', 'x/NUL.txt', 'white space',
                                  'a\x00b', 'a\nb', 'unicode-\u00e9', 'a'*129, '/'.join(['x']*17)])
def test_reference_rejects_unsafe_keys(key):
    with pytest.raises(ResultObjectError):
        ref(key)


@pytest.mark.parametrize('field,value', [('size', True), ('size', -1), ('size', MAX_OBJECT_BYTES+1),
                                         ('size', '10'), ('sha256', 'A'*64), ('sha256', '0'*63)])
def test_reference_rejects_invalid_bounds(field, value):
    raw = ref().to_dict()
    raw[field] = value
    with pytest.raises(ResultObjectError):
        ObjectRef.from_dict(raw)


def test_reference_is_strict_and_immutable():
    original = ref()
    assert ObjectRef.from_dict(original.to_dict()) == original
    with pytest.raises(ResultObjectError):
        ObjectRef.from_dict({**original.to_dict(), 'url': 'https://public'})
    with pytest.raises(ResultObjectError):
        ObjectRef.from_dict({'key': original.key})
    with pytest.raises(FrozenInstanceError):
        original.key = 'changed'


def test_roundtrip_idempotence_ranges_and_verified_deletion(store, tmp_path):
    objects, charges = store
    body = b'0123456789' * 100000
    source = tmp_path / 'source.bin'
    source.write_bytes(body)
    saved = objects.put('generation/pack.bin', source)
    assert saved == ref('generation/pack.bin', body)
    assert objects.put(saved.key, body) == saved
    assert objects.get(saved, len(body)) == body
    assert objects.read_range(saved, 17, 200) == body[17:217]
    assert objects.read_range(saved, len(body)-1, 1) == body[-1:]
    assert objects.exists(saved.key)
    assert objects.verify(saved) == ('"strong-etag"' if objects.client else None)
    objects.delete(saved)
    objects.delete(saved)
    assert not objects.exists(saved.key)
    assert charges[:4] == [('put', len(body)), ('get', len(body)), ('put', len(body)), ('get', len(body))]
    assert charges[-5:] == [('delete', 0), ('head', 0), ('delete', 0), ('head', 0), ('head', 0)]


def test_empty_object_is_supported(store):
    objects, _ = store
    saved = objects.put('empty', b'')
    assert objects.get(saved, 0) == b''
    with pytest.raises(ResultObjectError):
        objects.read_range(saved, 0, 1)


def test_conflicting_put_never_overwrites_existing_bytes(store):
    objects, _ = store
    saved = objects.put('immutable', b'original')
    with pytest.raises(ResultObjectError, match='checksum|length'):
        objects.put('immutable', b'changed!')
    assert objects.get(saved, 8) == b'original'


def test_reads_check_limits_before_charging_or_requesting(store):
    objects, charges = store
    saved = objects.put('limits', b'0123456789')
    previous = list(charges)
    for operation in (lambda: objects.get(saved, 9), lambda: objects.get(saved, True),
                      lambda: objects.read_range(saved, -1, 1), lambda: objects.read_range(saved, 0, 0),
                      lambda: objects.read_range(saved, 9, 2), lambda: objects.read_range(saved, 0, MAX_RANGE_BYTES+1),
                      lambda: objects.read_range(saved, True, 1)):
        with pytest.raises(ResultObjectError):
            operation()
    assert charges == previous


def test_full_reads_reject_changed_payload(store):
    objects, _ = store
    saved = objects.put('changed', b'original')
    if objects.client:
        objects.client.objects[objects.key(saved.key)] = b'altered!'
    else:
        (objects.local_root / objects.key(saved.key)).write_bytes(b'altered!')
    with pytest.raises(ResultObjectError, match='checksum'):
        objects.get(saved, 8)
    with pytest.raises(ResultObjectError, match='checksum'):
        objects.verify(saved)


def test_budget_failure_prevents_requests():
    client = FakeS3()
    charge = Mock(side_effect=RuntimeError('budget exhausted'))
    objects = ResultObjects(client=client, bucket='private-results', charge=charge)
    with pytest.raises(RuntimeError, match='budget exhausted'):
        objects.put('pack', b'payload')
    assert not client.calls


@pytest.mark.parametrize('change', [
    lambda r: r.update(ResponseMetadata={'HTTPStatusCode': 200}),
    lambda r: r.update(ContentRange='bytes 2-5/999'),
    lambda r: r.update(ContentRange='bytes 1-4/10'),
    lambda r: r.pop('ContentRange'),
    lambda r: r.update(ContentLength=5),
    lambda r: r.update(ContentLength=True),
    lambda r: r.update(Body=io.BytesIO(b'abc')),
    lambda r: r.update(Body=io.BytesIO(b'abcde')),
])
def test_range_rejects_wrong_status_headers_or_length_without_fallback(change):
    client = FakeS3()
    objects = ResultObjects(client=client, bucket='private-results')
    saved = objects.put('pack', b'0123456789')
    count = len(client.calls)
    client.rewrite_get = change
    with pytest.raises(ResultObjectError):
        objects.read_range(saved, 2, 4)
    assert len(client.calls) == count + 1
    assert client.calls[-1][1]['Range'] == 'bytes=2-5'
    assert client.bodies[-1].closed


@pytest.mark.parametrize('change', [lambda r: r.update(ContentLength=100),
                                   lambda r: r.update(Body=io.BytesIO(b'extra-long-content')),
                                   lambda r: r.update(ContentRange='bytes 0-9/10')])
def test_full_read_rejects_bad_headers_and_surplus_bytes(change):
    client = FakeS3()
    objects = ResultObjects(client=client, bucket='private-results')
    saved = objects.put('pack', b'0123456789')
    client.rewrite_get = change
    with pytest.raises(ResultObjectError):
        objects.get(saved, 10)
    assert client.bodies[-1].closed


def test_upload_failure_is_sanitized_and_never_retried():
    client = FakeS3()
    client.put_object = Mock(side_effect=TimeoutError('https://private-secret@cloud/path'))
    objects = ResultObjects(client=client, bucket='private-results')
    with pytest.raises(ResultObjectError) as failure:
        objects.put('pack', b'payload')
    client.put_object.assert_called_once()
    assert 'private-secret' not in ''.join(traceback.format_exception(failure.value))
    assert not client.calls


def test_delete_requires_confirmed_absence_and_404_is_narrow():
    client = FakeS3()
    objects = ResultObjects(client=client, bucket='private-results')
    saved = objects.put('pack', b'payload')
    client.keep_deleted = True
    with pytest.raises(ResultObjectError, match='verified'):
        objects.delete(saved)
    for code, status in [('AccessDenied', 403), ('NoSuchBucket', 404), ('InternalError', 500)]:
        client.head_object = Mock(side_effect=S3Error(code, status))
        with pytest.raises(ResultObjectError):
            objects.exists(saved.key)


def test_weak_copy_etag_is_rejected():
    client = FakeS3()
    objects = ResultObjects(client=client, bucket='private-results')
    saved = objects.put('pack', b'payload')
    client.rewrite_get = lambda r: r.update(ETag='W/"weak"')
    with pytest.raises(ResultObjectError, match='strong ETag'):
        objects.verify(saved)


def test_local_publish_is_complete_and_create_only(tmp_path, monkeypatch):
    objects = ResultObjects(local_root=tmp_path / 'objects')
    real_link = os.link
    observations = []

    def observed_link(source, destination, **kwargs):
        if os.name == 'nt':
            observations.append((Path(source).read_bytes(), Path(destination).exists()))
        else:
            fd = os.open(source, os.O_RDONLY, dir_fd=kwargs['src_dir_fd'])
            with os.fdopen(fd, 'rb') as stream:
                observations.append((stream.read(), False))
            with pytest.raises(FileNotFoundError):
                os.stat(destination, dir_fd=kwargs['dst_dir_fd'])
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(os, 'link', observed_link)
    saved = objects.put('generation/pack', b'complete bytes')
    assert observations == [(b'complete bytes', False)]
    assert objects.get(saved, saved.size) == b'complete bytes'
    assert not list(tmp_path.rglob('.upload-*'))


def test_local_directory_links_are_never_followed_or_deleted(tmp_path):
    root = tmp_path / 'objects'
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'pack').write_bytes(b'protected')
    (root / 'results').mkdir(parents=True)
    try:
        (root / 'results' / 'escape').symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(root / 'results' / 'escape'), str(outside)],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    objects = ResultObjects(local_root=root)
    saved = ref('escape/pack', b'protected')
    for operation in (lambda: objects.get(saved, 9), lambda: objects.read_range(saved, 0, 1),
                      lambda: objects.delete(saved), lambda: objects.exists(saved.key),
                      lambda: objects.put(saved.key, b'replacement')):
        with pytest.raises(ResultObjectError):
            operation()
    assert (outside / 'pack').read_bytes() == b'protected'


def test_local_file_symlink_is_not_deleted(tmp_path):
    root = tmp_path / 'objects'
    outside = tmp_path / 'protected'
    outside.write_bytes(b'protected')
    (root / 'results').mkdir(parents=True)
    objects = ResultObjects(local_root=root)
    leaf = root / 'results' / 'leaf'
    try:
        leaf.symlink_to(outside)
    except OSError:
        pytest.skip('Creating file symlinks requires Windows developer mode or privilege')
    with pytest.raises(ResultObjectError):
        objects.delete(ref('leaf', b'protected'))
    assert leaf.is_symlink()
    assert outside.read_bytes() == b'protected'


def credentials(**extra):
    return {'endpoint': 'https://' + 'a'*32 + '.r2.cloudflarestorage.com', 'bucket': 'backup-bucket',
            'access_key_id': 'access', 'secret_access_key': 'secret', **extra}


def settings(value=None, **extra):
    return SimpleNamespace(result_credentials_json=json.dumps(credentials() if value is None else value),
                           result_prefix='private/results', result_local_root='', **extra)


def test_factory_uses_explicit_credentials_one_attempt_and_closes(monkeypatch):
    client = FakeS3()
    factory = Mock(return_value=client)
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=factory))
    monkeypatch.setitem(sys.modules, 'botocore.config', SimpleNamespace(Config=lambda **kwargs: SimpleNamespace(**kwargs)))
    configured = settings(credentials(allowed_buckets=['backup-bucket', 'result-bucket'], token_name='operator', role='operator'),
                          result_bucket='result-bucket')
    objects = ResultObjects.from_settings(configured)
    assert objects.bucket == 'result-bucket'
    assert objects.key('generation/pack') == 'private/results/generation/pack'
    args = factory.call_args.kwargs
    assert args['aws_access_key_id'] == 'access'
    assert args['config'].retries == {'total_max_attempts': 1, 'mode': 'standard'}
    assert args['config'].connect_timeout == 5 and args['config'].read_timeout == 30
    objects.close()
    assert client.closed


@pytest.mark.parametrize('value,override', [
    (credentials(), 'another-bucket'),
    (credentials(allowed_buckets=['backup-bucket']), 'another-bucket'),
    (credentials(allowed_buckets='backup-bucket'), ''),
    (credentials(endpoint='http://localhost'), ''),
    (credentials(endpoint=[]), ''),
    (credentials(endpoint='https://secret@example.com'), ''),
    (credentials(endpoint='https://'+'a'*32+'.r2.cloudflarestorage.com?secret'), ''),
    (credentials(endpoint='https://'+'a'*32+'.r2.cloudflarestorage.com/elsewhere'), ''),
    (credentials(secret_access_key=''), ''),
])
def test_factory_rejects_unsafe_credentials_before_sdk_creation(value, override, monkeypatch):
    factory = Mock()
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=factory))
    with pytest.raises(ResultObjectError):
        ResultObjects.from_settings(settings(value, result_bucket=override))
    factory.assert_not_called()


def test_local_configuration_is_explicit_and_not_serverless(tmp_path):
    configured = SimpleNamespace(result_credentials_json='', result_prefix='results', result_local_root=tmp_path,
                                 deployment_mode='vm')
    assert ResultObjects.from_settings(configured).local_root == tmp_path
    configured.deployment_mode = 'serverless'
    with pytest.raises(ResultObjectError):
        ResultObjects.from_settings(configured)


@pytest.mark.parametrize('status,code', [(500, 'InternalError'), (301, 'PermanentRedirect'),
                                       (400, 'AuthorizationHeaderMalformed')])
def test_real_sdk_makes_one_request_even_when_s3_redirects(status, code, monkeypatch, tmp_path):
    boto3 = pytest.importorskip('boto3')
    requests = []
    payloads = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            requests.append(self.path)
            payloads.append(self.rfile.read(int(self.headers['Content-Length'])))
            body = f'<Error><Code>{code}</Code><Region>other-region</Region></Error>'.encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'application/xml')
            self.send_header('x-amz-bucket-region', 'other-region')
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            requests.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    original_factory = boto3.client

    def local_factory(*args, **kwargs):
        kwargs['endpoint_url'] = f'http://127.0.0.1:{server.server_port}'
        return original_factory(*args, **kwargs)

    monkeypatch.setattr(boto3, 'client', local_factory)
    charges = []
    objects = ResultObjects.from_settings(settings(), charge=lambda kind, **kw: charges.append((kind, kw['bytes'])))
    source = tmp_path / 'source.bin'
    source.write_bytes(b'payload')
    try:
        with pytest.raises(ResultObjectError):
            objects.put('pack', source)
        assert requests == ['/backup-bucket/private/results/pack']
        assert payloads == [b'payload']
        assert charges == [('put', 7)]
    finally:
        objects.close()
        server.shutdown()
        server.server_close()
        server_thread.join()
