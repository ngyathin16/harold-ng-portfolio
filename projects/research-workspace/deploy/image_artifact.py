"""Validate the exact local image archives before granting a registry upload.

No archive member is extracted. Docker schema2 manifests and gzip layers are
required so a digest-preserving copy cannot silently change their storage size.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tarfile


MAX_BYTES = 2 * 1024**3
MAX_JSON = 32 * 1024**2
MAX_METADATA = 1024**2
MAX_LAYERS = 128
MAX_MEMBERS = MAX_LAYERS + 8
CHUNK = 1024**2
MANIFEST = 'application/vnd.docker.distribution.manifest.v2+json'
CONFIG = 'application/vnd.docker.container.image.v1+json'
LAYER = 'application/vnd.docker.image.rootfs.diff.tar.gzip'
INDEX = 'application/vnd.oci.image.index.v1+json'
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
BLOB = re.compile(r'blobs/sha256/[0-9a-f]{64}')
REPOSITORY = re.compile(r'[a-z]+-[a-z]+[0-9]+-docker\.pkg\.dev/[a-z][a-z0-9-]{4,28}[a-z0-9]/research-(staging|production)')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def whole(value, maximum=MAX_BYTES):
    return type(value) is int and 0 <= value <= maximum


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))


def regular(path):
    require(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), 'Expected a regular local file')


def file_hash(path, maximum=MAX_BYTES):
    regular(path)
    size, digest = 0, hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(CHUNK):
            size += len(block)
            require(size <= maximum, 'File exceeds its bound')
            digest.update(block)
    return size, digest.hexdigest()


def members(stream, size):
    """Read only bounded USTAR headers; reject links, PAX and GNU extensions."""
    found = {}
    count = 0
    while stream.tell() < size:
        header = stream.read(512)
        require(len(header) == 512, 'Truncated tar header')
        if header == bytes(512):
            require(512 <= size - stream.tell() <= 10240, 'Invalid tar end padding length')
            tail = stream.read()
            require(512 <= len(tail) <= 10240 and not any(tail), 'Invalid tar end or trailing content')
            return found
        count += 1
        require(count <= MAX_MEMBERS, 'Too many archive members')
        require(header[156:157] in (b'0', b'\0', b'5') and header[257:265] == b'ustar\00000',
                'Only regular USTAR members and directories are permitted')
        item = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
        name = item.name.rstrip('/') if item.isdir() else item.name
        require(name not in found and not item.linkname and not item.sparse, 'Duplicate or linked archive member')
        require(whole(item.size) and stream.tell() + ((item.size + 511) // 512) * 512 <= size, 'Truncated or oversized archive member')
        require(name in ('oci-layout', 'index.json', 'blobs', 'blobs/sha256') or BLOB.fullmatch(name), 'Unexpected archive member path')
        if item.isdir():
            require(name in ('blobs', 'blobs/sha256') and item.size == 0, 'Unexpected archive directory')
            found[name] = dict(directory=True)
            continue
        require(name not in ('blobs', 'blobs/sha256'), 'Blob directory is not a directory')
        offset, digest, remaining = stream.tell(), hashlib.sha256(), item.size
        while remaining:
            block = stream.read(min(CHUNK, remaining))
            require(bool(block), 'Truncated archive content')
            digest.update(block); remaining -= len(block)
        padding = stream.read((-item.size) % 512)
        require(not any(padding), 'Nonzero tar member padding')
        actual = digest.hexdigest()
        require(not BLOB.fullmatch(name) or name.rsplit('/', 1)[1] == actual, 'Blob content hash differs from its name')
        found[name] = dict(offset=offset, size=item.size, sha256=actual)
    raise ValueError('Archive has no complete end marker')


class Slice(io.RawIOBase):
    def __init__(self, stream, size):
        self.stream, self.remaining = stream, size

    def read(self, size=-1):
        size = self.remaining if size < 0 else min(size, self.remaining)
        raw = self.stream.read(size)
        self.remaining -= len(raw)
        return raw


def validate_archive(path, image, commit, *, max_uncompressed_bytes=MAX_BYTES):
    path = Path(path)
    require(re.fullmatch(r'[0-9a-f]{40}', commit), 'A complete source commit is required')
    repository, kind = image.rsplit('/', 1)
    require(REPOSITORY.fullmatch(repository) and kind in ('service:' + commit, 'operator:' + commit), 'Image destination differs')
    require(whole(max_uncompressed_bytes), 'Invalid uncompressed limit')
    archive_size, archive_sha = file_hash(path)
    require(archive_size > 0 and archive_size % 512 == 0, 'Invalid archive length')
    with path.open('rb') as stream:
        entries = members(stream, archive_size)

        def document(name, maximum):
            item = entries.get(name)
            require(item is not None and not item.get('directory') and item['size'] <= maximum, 'Missing or oversized JSON member')
            stream.seek(item['offset'])
            return parse_json(stream.read(item['size']))

        def descriptor(value, media_type, maximum=MAX_BYTES):
            require(type(value) is dict and set(value) <= {'mediaType', 'size', 'digest', 'annotations', 'platform'}, 'External or unsupported descriptor')
            require(value.get('mediaType') == media_type and isinstance(value.get('digest'), str) and DIGEST.fullmatch(value['digest']), 'Descriptor type or digest differs')
            require(whole(value.get('size'), maximum), 'Invalid descriptor size')
            name = 'blobs/sha256/' + value['digest'][7:]
            require(name in entries and entries[name].get('size') == value['size'], 'Descriptor content is absent or has the wrong size')
            return name

        require(document('oci-layout', MAX_METADATA) == {'imageLayoutVersion': '1.0.0'}, 'Unsupported OCI layout')
        index = document('index.json', MAX_METADATA)
        require(type(index) is dict and type(index.get('schemaVersion')) is int and index['schemaVersion'] == 2
                and index.get('mediaType') in (None, INDEX), 'Invalid OCI index')
        require(type(index.get('manifests')) is list and len(index['manifests']) == 1, 'Exactly one image manifest is required')
        top = index['manifests'][0]
        manifest_name = descriptor(top, MANIFEST, MAX_METADATA)
        annotations = top.get('annotations', {})
        require(annotations.get('io.containerd.image.name') == image and annotations.get('org.opencontainers.image.ref.name') == commit,
                'Archive tag differs from the exact destination and commit')
        platform = top.get('platform')
        if platform is not None:
            require(platform.get('os') == 'linux' and platform.get('architecture') == 'amd64' and platform.get('variant', '') == '', 'Manifest platform differs')
        manifest = document(manifest_name, MAX_METADATA)
        require(type(manifest) is dict and set(manifest) <= {'schemaVersion', 'mediaType', 'config', 'layers', 'annotations'}
                and type(manifest.get('schemaVersion')) is int and manifest['schemaVersion'] == 2
                and manifest.get('mediaType') == MANIFEST, 'Unsupported manifest')
        layers = manifest.get('layers')
        require(type(layers) is list and 1 <= len(layers) <= MAX_LAYERS, 'Invalid layer count')
        config_name = descriptor(manifest['config'], CONFIG, MAX_JSON)
        config = document(config_name, MAX_JSON)
        require(type(config) is dict and config.get('os') == 'linux' and config.get('architecture') == 'amd64'
                and config.get('variant', '') == '', 'Config platform differs')
        labels = config.get('config', {}).get('Labels', {})
        require(all(labels.get(key) == value for key, value in {
            'org.opencontainers.image.revision': commit, 'org.research.source-state': 'clean',
            'org.research.download-contract': 'disabled-v1'}.items()), 'Required source or download-contract labels differ')
        rootfs = config.get('rootfs', {})
        diff_ids = rootfs.get('diff_ids')
        require(rootfs.get('type') == 'layers' and type(diff_ids) is list and len(diff_ids) == len(layers)
                and all(isinstance(value, str) and DIGEST.fullmatch(value) for value in diff_ids), 'Layer diff IDs differ')
        expected = {'oci-layout', 'index.json', manifest_name, config_name}
        blobs = {manifest['config']['digest']: dict(manifest['config'])}
        uncompressed = 0
        for layer, diff_id in zip(layers, diff_ids):
            name = descriptor(layer, LAYER)
            expected.add(name)
            blobs[layer['digest']] = dict(layer)
            stream.seek(entries[name]['offset'])
            digest = hashlib.sha256()
            with gzip.GzipFile(fileobj=Slice(stream, layer['size']), mode='rb') as decoded:
                while block := decoded.read(CHUNK):
                    uncompressed += len(block)
                    require(uncompressed <= max_uncompressed_bytes, 'Uncompressed image layers exceed the pair allowance')
                    digest.update(block)
            require('sha256:' + digest.hexdigest() == diff_id, 'Uncompressed layer hash differs from config')
        require({name for name, item in entries.items() if not item.get('directory')} == expected, 'Archive contains unreferenced content')
    require(uncompressed > 0, 'Image layers are empty')
    require(file_hash(path) == (archive_size, archive_sha), 'Archive changed during validation')
    return dict(image=image, manifest_digest=top['digest'], manifest_bytes=top['size'],
                archive_bytes=archive_size, archive_sha256=archive_sha, uncompressed_layer_bytes=uncompressed,
                blobs=sorted(blobs.values(), key=lambda value: value['digest']))


def validate_pair(service, operator, repository, commit):
    artifacts, remaining = {}, MAX_BYTES
    for kind, path in (('service', service), ('operator', operator)):
        artifacts[kind] = validate_archive(path, repository + '/' + kind + ':' + commit, commit, max_uncompressed_bytes=remaining)
        remaining -= artifacts[kind]['uncompressed_layer_bytes']
    require(sum(value['archive_bytes'] for value in artifacts.values()) <= MAX_BYTES, 'Combined archives exceed the storage allowance')
    return dict(schema_version=1, storage_accounting='full-compressed-oci-archives-v1', app_commit=commit,
                repository=repository, artifacts=artifacts, copy_verified=False)


def check_archive(path, artifact):
    require(file_hash(Path(path)) == (artifact['archive_bytes'], artifact['archive_sha256']), 'Archive changed after storage qualification')


def verify_copy_directory(path, artifact):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink(), 'Expected a local copy directory')
    expected = {'manifest.json': (artifact['manifest_bytes'], artifact['manifest_digest'][7:])}
    expected.update({value['digest'][7:]: (value['size'], value['digest'][7:]) for value in artifact['blobs']})
    require({item.name for item in path.iterdir()} == set(expected) | {'version'}, 'Local copy content inventory differs')
    regular(path/'version')
    require((path/'version').stat().st_size == 33, 'Invalid directory transport marker length')
    require((path/'version').read_bytes() == b'Directory Transport Version: 1.1\n', 'Unsupported directory transport')
    for name, identity in expected.items():
        require(file_hash(path/name) == identity, 'Local digest-preserving copy changed content')


def storage_projection(images, retained_bytes, report):
    require(type(images) is dict and all(type(uri) is str and whole(size) for uri, size in images.items()), 'Invalid registry inventory')
    require(whole(retained_bytes), 'Invalid retained storage floor')
    require(report.get('schema_version') == 1 and report.get('storage_accounting') == 'full-compressed-oci-archives-v1'
            and report.get('copy_verified') is True, 'Local digest-preserving copy must be qualified first')
    require(set(report['artifacts']) == {'service', 'operator'}, 'Two qualified artifacts are required')
    require(all(whole(item.get('archive_bytes')) and item['archive_bytes'] > 0
                and whole(item.get('uncompressed_layer_bytes')) and item['uncompressed_layer_bytes'] > 0
                for item in report['artifacts'].values()), 'Artifact sizes must be positive bounded integers')
    new_bytes = sum(item['archive_bytes'] for item in report['artifacts'].values())
    require(sum(item['uncompressed_layer_bytes'] for item in report['artifacts'].values()) <= MAX_BYTES,
            'Uncompressed image pair exceeds its independent allowance')
    require(whole(new_bytes) and new_bytes > 0, 'Invalid archive storage allowance')
    observed = sum(images.values())
    accounted = max(observed, retained_bytes)
    require(accounted + new_bytes <= MAX_BYTES, 'Retained storage plus exact archives exceed the 2 GiB allowance')
    require(len(images) + 2 <= 4, 'Retained image count exceeds the four-manifest allowance')
    return dict(existing_bytes=observed, existing_images=len(images), retained_storage_bytes=retained_bytes,
                accounted_existing_bytes=accounted, projected_bytes=accounted + new_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('validate', 'copy-check', 'check-files'))
    parser.add_argument('--service', required=True)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--repository')
    parser.add_argument('--commit')
    parser.add_argument('--skopeo-version')
    args = parser.parse_args()
    report_path = Path(args.report)
    if args.mode == 'validate':
        report = validate_pair(args.service, args.operator, args.repository, args.commit)
    else:
        require(report_path.stat().st_size <= MAX_METADATA, 'Artifact report exceeds its bound')
        report = parse_json(report_path.read_bytes())
        require(report.get('schema_version') == 1 and report.get('storage_accounting') == 'full-compressed-oci-archives-v1'
                and set(report.get('artifacts', {})) == {'service', 'operator'}, 'Unsupported artifact report')
        for kind in ('service', 'operator'):
            if args.mode == 'copy-check':
                verify_copy_directory(getattr(args, kind), report['artifacts'][kind])
            else:
                check_archive(getattr(args, kind), report['artifacts'][kind])
        if args.mode == 'check-files':
            require(report.get('copy_verified') is True, 'Local copy compatibility was not qualified')
            return
        require(isinstance(args.skopeo_version, str) and 0 < len(args.skopeo_version) <= 200, 'Record the actual Skopeo version')
        report.update(copy_verified=True, skopeo_version=args.skopeo_version)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
