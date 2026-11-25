"""Bounded immutable private objects; database receipts control their lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
import uuid


MAX_OBJECT_BYTES = 256 * 1024 * 1024
MAX_RANGE_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
MAX_KEY_LENGTH = 512
_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                   *(f'LPT{i}' for i in range(1, 10))}


class ResultObjectError(ValueError):
    pass


def _key(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_KEY_LENGTH:
        raise ResultObjectError('Invalid result object key')
    parts = value.split('/')
    if len(parts) > 16 or any(not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}', part)
                             or part.endswith('.') or part.split('.')[0].upper() in _RESERVED_NAMES
                             for part in parts):
        raise ResultObjectError('Invalid result object key')
    return value


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


@dataclass(frozen=True)
class ObjectRef:
    key: str
    size: int
    sha256: str

    def __post_init__(self):
        _key(self.key)
        if not _integer(self.size, 0, MAX_OBJECT_BYTES):
            raise ResultObjectError('Invalid result object size')
        if not isinstance(self.sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', self.sha256):
            raise ResultObjectError('Invalid result object checksum')

    def to_dict(self) -> dict:
        return {'key': self.key, 'size': self.size, 'sha256': self.sha256}

    @classmethod
    def from_dict(cls, value) -> ObjectRef:
        if not isinstance(value, dict) or set(value) != {'key', 'size', 'sha256'}:
            raise ResultObjectError('Invalid result object reference')
        return cls(**value)


def _unique_json(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ResultObjectError('Duplicate result credential field')
        value[key] = item
    return value


def _bucket(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', value):
        raise ResultObjectError('Invalid result bucket')
    return value


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _reject_redirects(response=None, **kwargs):
    if response is None:
        return
    status = getattr(response[0], 'status_code', 0)
    code = response[1].get('Error', {}).get('Code')
    if 300 <= status < 400 or code in ('PermanentRedirect', 'AuthorizationHeaderMalformed', '301', '400'):
        raise ResultObjectError('Result storage redirects are not permitted')


def _windows_handle(path, *, directory=False):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD)

    class Attributes(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]

    access = 0x80 if directory else 0x80000000
    sharing = 3 if directory else 1  # Pin directories against renames; readers also exclude writes.
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = kernel.CreateFileW(str(path), access, sharing, None, 3, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        if ctypes.get_last_error() in (2, 3):
            raise FileNotFoundError
        raise ResultObjectError('Cannot open local result storage')
    attributes = Attributes()
    if (not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes))
            or attributes.attributes & 0x400 or bool(attributes.attributes & 0x10) != directory):
        kernel.CloseHandle(handle)
        raise ResultObjectError('Local result storage must not contain links or special files')
    return handle, kernel


@contextmanager
def _regular_file(path, *, parent_fd=None):
    descriptor = None
    try:
        if os.name == 'nt':
            import msvcrt
            handle, kernel = _windows_handle(path)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
        else:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ResultObjectError('Result object must be a regular file')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            yield stream
    except OSError:
        raise ResultObjectError('Cannot read local result object') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class _BoundedSource:
    def __init__(self, stream, size):
        self.stream, self.size = stream, size

    def read(self, size=-1):
        remaining = self.size - self.tell()
        amount = remaining if size < 0 else min(size, remaining)
        block = self.stream.read(amount)
        if amount and not block:
            raise ResultObjectError('Result source changed during upload')
        return block

    def tell(self):
        return self.stream.tell()

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.tell() + offset if whence == 1 else self.size + offset
        if whence not in (0, 1, 2) or not 0 <= target <= self.size:
            raise ResultObjectError('Invalid result upload stream position')
        return self.stream.seek(target)


class ResultObjects:
    def __init__(self, *, local_root=None, client=None, bucket='', endpoint='', prefix='results', charge=None):
        if (local_root is None) == (client is None):
            raise ResultObjectError('Configure exactly one result object backend')
        if not isinstance(prefix, str):
            raise ResultObjectError('Invalid result namespace')
        self.prefix = _key(prefix.removesuffix('/'))
        if len(self.prefix) > 128:
            raise ResultObjectError('Result namespace is too long')
        self.local_root = Path(os.path.abspath(local_root)) if local_root is not None else None
        self.client, self.bucket, self.endpoint = client, _bucket(bucket) if client is not None else '', endpoint
        self.charge = charge if charge is not None else lambda kind, **kwargs: None
        if not callable(self.charge):
            raise ResultObjectError('Invalid result operation accounting callback')
        if client is not None and hasattr(client, 'meta'):
            if client.meta.config.retries.get('total_max_attempts') != 1:
                raise ResultObjectError('Result storage requires exactly one SDK attempt')
            client.meta.events.register_first('needs-retry.s3', _reject_redirects,
                                              unique_id='research-results-no-redirect')

    @classmethod
    def from_settings(cls, settings, *, charge=None):
        root = getattr(settings, 'result_local_root', '')
        credentials = getattr(settings, 'result_credentials_json', '')
        prefix = getattr(settings, 'result_prefix', 'results')
        if root:
            if credentials or getattr(settings, 'deployment_mode', 'vm') == 'serverless':
                raise ResultObjectError('Local result storage cannot be combined with cloud configuration')
            return cls(local_root=root, prefix=prefix, charge=charge)
        if not isinstance(credentials, str) or not 1 <= len(credentials) <= 16384:
            raise ResultObjectError('Result object credentials are required')
        try:
            value = json.loads(credentials, object_pairs_hook=_unique_json)
            if not isinstance(value, dict):
                raise ValueError
            endpoint = value['endpoint']
            if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 256:
                raise ValueError
            parsed = urlsplit(endpoint)
            if (parsed.scheme != 'https' or not parsed.hostname
                    or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,80}\.r2\.cloudflarestorage\.com', parsed.hostname)
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in ('', '/') or parsed.port not in (None, 443)):
                raise ValueError
            primary_bucket = _bucket(value['bucket'])
            bucket = _bucket(getattr(settings, 'result_bucket', '') or primary_bucket)
            allowed = value.get('allowed_buckets')
            if allowed is not None:
                if not isinstance(allowed, list) or not 1 <= len(allowed) <= 32:
                    raise ValueError
                allowed = {_bucket(item) for item in allowed}
                if primary_bucket not in allowed or bucket not in allowed:
                    raise ValueError
            elif bucket != primary_bucket:
                raise ValueError
            for field in ('access_key_id', 'secret_access_key'):
                if not isinstance(value[field], str) or not re.fullmatch(r'[!-~]{1,512}', value[field]):
                    raise ValueError
        except (KeyError, TypeError, ValueError, RecursionError):
            raise ResultObjectError('Invalid result object credentials or bucket authorization') from None
        try:
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', endpoint_url='https://' + parsed.hostname, region_name='auto',
                aws_access_key_id=value['access_key_id'], aws_secret_access_key=value['secret_access_key'],
                config=Config(retries={'total_max_attempts': 1, 'mode': 'standard'}, connect_timeout=5,
                              read_timeout=30, max_pool_connections=16, signature_version='s3v4',
                              s3={'addressing_style': 'path', 'payload_signing_enabled': False},
                              request_checksum_calculation='when_required',
                              response_checksum_validation='when_required'))
        except Exception:
            raise ResultObjectError('Cannot configure private result storage') from None
        return cls(client=client, bucket=bucket, endpoint='https://' + parsed.hostname, prefix=prefix, charge=charge)

    def key(self, logical_key: str) -> str:
        return self.prefix + '/' + _key(logical_key)

    @contextmanager
    def _parent(self, logical_key, *, create=False):
        physical = self.key(logical_key)
        parts = self.local_root.parts[1:] + tuple(physical.split('/'))
        handles = []
        try:
            if os.name == 'nt':
                directory = Path(self.local_root.anchor)
                handle, kernel = _windows_handle(directory, directory=True)
                handles.append((handle, kernel))
                for part in parts[:-1]:
                    directory /= part
                    if create:
                        directory.mkdir(exist_ok=True, mode=0o700)
                    handle, kernel = _windows_handle(directory, directory=True)
                    handles.append((handle, kernel))
                yield directory / parts[-1], None
            else:
                descriptor = os.open(self.local_root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                handles.append(descriptor)
                for part in parts[:-1]:
                    if create:
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                    descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    handles.append(descriptor)
                yield parts[-1], descriptor
        except FileNotFoundError:
            raise
        except OSError:
            raise ResultObjectError('Cannot access local result namespace') from None
        finally:
            for handle in reversed(handles):
                if os.name == 'nt':
                    handle[1].CloseHandle(handle[0])
                else:
                    os.close(handle)

    @contextmanager
    def _source(self, source):
        if isinstance(source, bytes):
            if len(source) > MAX_OBJECT_BYTES:
                raise ResultObjectError('Result object exceeds the upload limit')
            with io.BytesIO(source) as stream:
                yield stream, len(source), hashlib.sha256(source).hexdigest()
            return
        if not isinstance(source, Path):
            raise ResultObjectError('Result upload requires bytes or a Path')
        with _regular_file(source) as stream:
            size = os.fstat(stream.fileno()).st_size
            if not _integer(size, 0, MAX_OBJECT_BYTES):
                raise ResultObjectError('Result object exceeds the upload limit')
            digest = hashlib.sha256()
            self._consume(stream, size, digest=digest)
            stream.seek(0)
            yield _BoundedSource(stream, size), size, digest.hexdigest()

    @staticmethod
    def _consume(stream, expected, *, digest=None, collect=False):
        chunks = [] if collect else None
        remaining = expected
        while remaining:
            requested = min(CHUNK_BYTES, remaining)
            block = stream.read(requested)
            if not isinstance(block, bytes) or not block or len(block) > requested:
                raise ResultObjectError('Result object length mismatch')
            remaining -= len(block)
            if digest is not None:
                digest.update(block)
            if collect:
                chunks.append(block)
        if stream.read(1) != b'':
            raise ResultObjectError('Result object exceeds its declared length')
        return b''.join(chunks) if collect else None

    def put(self, key: str, source: bytes | Path) -> ObjectRef:
        self.key(key)
        with self._source(source) as (stream, size, checksum):
            ref = ObjectRef(key, size, checksum)
            self.charge('put', bytes=size)
            if self.client is not None:
                try:
                    response = self.client.put_object(Bucket=self.bucket, Key=self.key(key), Body=stream,
                                                      ContentLength=size, IfNoneMatch='*')
                    if response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200:
                        raise ResultObjectError('Unexpected result upload response')
                except Exception as error:
                    status = getattr(error, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode')
                    code = getattr(error, 'response', {}).get('Error', {}).get('Code')
                    if status != 412 and code != 'PreconditionFailed':
                        raise ResultObjectError('Result object upload failed') from None
            else:
                self._put_local(ref, stream)
        self.verify(ref)
        return ref

    def _put_local(self, ref, source):
        with self._parent(ref.key, create=True) as (destination, descriptor):
            temporary = '.upload-' + uuid.uuid4().hex
            if descriptor is None:
                temporary = destination.with_name(temporary)
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
                fd = os.open(temporary, flags, 0o600, dir_fd=descriptor)
                with os.fdopen(fd, 'wb') as target:
                    remaining = ref.size
                    while remaining:
                        block = source.read(min(CHUNK_BYTES, remaining))
                        if not block or len(block) > remaining:
                            raise ResultObjectError('Result source changed during upload')
                        target.write(block)
                        remaining -= len(block)
                    target.flush()
                    os.fsync(target.fileno())
                try:
                    os.link(temporary, destination, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
                except FileExistsError:
                    pass
                if descriptor is not None:
                    os.fsync(descriptor)
            finally:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass

    @contextmanager
    def _read(self, ref, *, start=None, length=None):
        if not isinstance(ref, ObjectRef):
            raise ResultObjectError('Invalid result object reference')
        self.charge('get' if start is None else 'range', bytes=ref.size if start is None else length)
        if self.client is None:
            try:
                with self._parent(ref.key) as (path, descriptor), _regular_file(path, parent_fd=descriptor) as stream:
                    if os.fstat(stream.fileno()).st_size != ref.size:
                        raise ResultObjectError('Result object length mismatch')
                    if start is not None:
                        stream.seek(start)
                    yield stream, None
            except FileNotFoundError:
                raise ResultObjectError('Local result object is missing') from None
            return
        arguments = {'Bucket': self.bucket, 'Key': self.key(ref.key)}
        if start is not None:
            arguments['Range'] = f'bytes={start}-{start + length - 1}'
        response = None
        try:
            response = self.client.get_object(**arguments)
            status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            expected = ref.size if start is None else length
            if status != (200 if start is None else 206) or type(response.get('ContentLength')) is not int:
                raise ResultObjectError('Unexpected result read response')
            if response['ContentLength'] != expected:
                raise ResultObjectError('Result object length mismatch')
            if start is None:
                if response.get('ContentRange'):
                    raise ResultObjectError('Unexpected partial result response')
            elif response.get('ContentRange') != f'bytes {start}-{start + length - 1}/{ref.size}':
                raise ResultObjectError('Result object range mismatch')
            yield response['Body'], response.get('ETag')
        except ResultObjectError:
            raise
        except Exception:
            raise ResultObjectError('Result object read failed') from None
        finally:
            if response is not None and response.get('Body') is not None:
                try:
                    response['Body'].close()
                except Exception:
                    pass

    def verify(self, ref: ObjectRef) -> str | None:
        digest = hashlib.sha256()
        with self._read(ref) as (stream, etag):
            self._consume(stream, ref.size, digest=digest)
        if digest.hexdigest() != ref.sha256:
            raise ResultObjectError('Result object checksum mismatch')
        if self.client is not None and (not isinstance(etag, str) or not re.fullmatch(r'"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"', etag)):
            raise ResultObjectError('Result object requires a strong ETag')
        return etag

    def get(self, ref: ObjectRef, max_bytes: int) -> bytes:
        if (not isinstance(ref, ObjectRef) or not _integer(max_bytes, 0, MAX_OBJECT_BYTES)
                or ref.size > max_bytes):
            raise ResultObjectError('Result object exceeds the read limit')
        digest = hashlib.sha256()
        with self._read(ref) as (stream, _):
            value = self._consume(stream, ref.size, digest=digest, collect=True)
        if digest.hexdigest() != ref.sha256:
            raise ResultObjectError('Result object checksum mismatch')
        return value

    def read_range(self, ref: ObjectRef, start: int, length: int) -> bytes:
        if (not isinstance(ref, ObjectRef) or not _integer(start, 0, MAX_OBJECT_BYTES)
                or not _integer(length, 1, MAX_RANGE_BYTES) or start + length > ref.size):
            raise ResultObjectError('Invalid result object range')
        with self._read(ref, start=start, length=length) as (stream, _):
            if self.client is not None:
                return self._consume(stream, length, collect=True)
            value = stream.read(length)
            if len(value) != length:
                raise ResultObjectError('Result object range length mismatch')
            return value

    def delete(self, ref: ObjectRef) -> None:
        if not isinstance(ref, ObjectRef):
            raise ResultObjectError('Invalid result object reference')
        self.charge('delete', bytes=0)
        if self.client is not None:
            try:
                response = self.client.delete_object(Bucket=self.bucket, Key=self.key(ref.key))
                if response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 204:
                    raise ResultObjectError('Unexpected result deletion response')
            except Exception:
                raise ResultObjectError('Result object deletion failed') from None
        else:
            try:
                with self._parent(ref.key) as (path, descriptor):
                    info = os.stat(path, dir_fd=descriptor, follow_symlinks=False)
                    if _is_link(info) or not stat.S_ISREG(info.st_mode):
                        raise ResultObjectError('Cannot delete a link or special result object')
                    os.unlink(path, dir_fd=descriptor)
            except FileNotFoundError:
                pass
        if self.exists(ref.key):
            raise ResultObjectError('Result object deletion could not be verified')

    def exists(self, key: str) -> bool:
        self.key(key)
        self.charge('head', bytes=0)
        if self.client is not None:
            try:
                response = self.client.head_object(Bucket=self.bucket, Key=self.key(key))
                if response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200:
                    raise ResultObjectError('Unexpected result existence response')
                return True
            except Exception as error:
                response = getattr(error, 'response', {})
                if (response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 404
                        and response.get('Error', {}).get('Code') in ('404', 'NotFound', 'NoSuchKey')):
                    return False
                raise ResultObjectError('Cannot verify result object existence') from None
        try:
            with self._parent(key) as (path, descriptor):
                info = os.stat(path, dir_fd=descriptor, follow_symlinks=False)
                if _is_link(info) or not stat.S_ISREG(info.st_mode):
                    raise ResultObjectError('Result object must not be a link or special file')
                return True
        except FileNotFoundError:
            return False

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                raise ResultObjectError('Cannot close result object transport') from None
