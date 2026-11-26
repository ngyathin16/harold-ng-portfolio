"""Cost and identity boundaries for local, digest-preserved OCI archives."""
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

import pytest

from deploy import image_artifact as module


COMMIT = 'a' * 40
REPOSITORY = 'asia-southeast1-docker.pkg.dev/research-fixture/research-staging'


def packed(value):
    return json.dumps(value, separators=(',', ':')).encode()


def descriptor(raw, media):
    return dict(mediaType=media, digest='sha256:' + hashlib.sha256(raw).hexdigest(), size=len(raw))


def fixture_files(kind='service', *, config_change=None, manifest_change=None, index_change=None):
    image = REPOSITORY + '/' + kind + ':' + COMMIT
    raw_layer = b'fixture filesystem layer' * 40
    layer = gzip.compress(raw_layer, mtime=0)
    config = dict(os='linux', architecture='amd64', config={'Labels': {
        'org.opencontainers.image.revision': COMMIT, 'org.research.source-state': 'clean',
        'org.research.download-contract': 'disabled-v1'}},
        rootfs=dict(type='layers', diff_ids=['sha256:' + hashlib.sha256(raw_layer).hexdigest()]))
    if config_change:
        config_change(config)
    config_raw = packed(config)
    manifest = dict(schemaVersion=2, mediaType=module.MANIFEST,
                    config=descriptor(config_raw, module.CONFIG), layers=[descriptor(layer, module.LAYER)])
    if manifest_change:
        manifest_change(manifest)
    manifest_raw = packed(manifest)
    top = descriptor(manifest_raw, module.MANIFEST)
    top.update(platform=dict(os='linux', architecture='amd64'), annotations={
        'io.containerd.image.name': image, 'org.opencontainers.image.ref.name': COMMIT})
    index = dict(schemaVersion=2, mediaType=module.INDEX, manifests=[top])
    if index_change:
        index_change(index)
    return [('oci-layout', packed({'imageLayoutVersion': '1.0.0'})), ('index.json', packed(index)),
            *[('blobs/sha256/' + hashlib.sha256(raw).hexdigest(), raw) for raw in (config_raw, manifest_raw, layer)]]


def write_archive(path, files, *, extra=()):
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name in ('blobs', 'blobs/sha256'):
            item = tarfile.TarInfo(name); item.type = tarfile.DIRTYPE
            archive.addfile(item)
        for name, raw in files:
            item = tarfile.TarInfo(name); item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
        for item, raw in extra:
            archive.addfile(item, io.BytesIO(raw))
    return path


def validate(path, kind='service', **kwargs):
    return module.validate_archive(path, REPOSITORY + '/' + kind + ':' + COMMIT, COMMIT, **kwargs)


def copy_directory(path, files, artifact):
    path.mkdir()
    by_name = dict(files)
    (path/'version').write_bytes(b'Directory Transport Version: 1.1\n')
    manifest_hex = artifact['manifest_digest'][7:]
    (path/'manifest.json').write_bytes(by_name['blobs/sha256/' + manifest_hex])
    for blob in artifact['blobs']:
        hex_digest = blob['digest'][7:]
        (path/hex_digest).write_bytes(by_name['blobs/sha256/' + hex_digest])
    return path


def test_valid_archive_measures_exact_bytes_and_preserves_manifest_and_every_blob(tmp_path):
    files = fixture_files()
    archive = write_archive(tmp_path/'service.tar', files)
    value = validate(archive)
    assert value['archive_bytes'] == archive.stat().st_size
    assert value['archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert value['uncompressed_layer_bytes'] == len(b'fixture filesystem layer' * 40)
    assert len(value['blobs']) == 2
    assert value['manifest_media_type'] == 'application/vnd.oci.image.manifest.v1+json'
    assert {blob['mediaType'] for blob in value['blobs']} == {
        'application/vnd.oci.image.config.v1+json', 'application/vnd.oci.image.layer.v1.tar+gzip'}
    module.verify_copy_directory(copy_directory(tmp_path/'copy', files, value), value)
    module.check_archive(archive, value)


def test_docker_schema2_inside_oci_archive_is_rejected_before_copy(tmp_path, monkeypatch):
    # Ubuntu 24.04 Skopeo 1.13.3 rejects these media types in oci-archive sources.
    # The validator must reject them before reaching any copy or authentication.
    with monkeypatch.context() as old_format:
        old_format.setattr(module, 'MANIFEST', 'application/vnd.docker.distribution.manifest.v2+json')
        old_format.setattr(module, 'CONFIG', 'application/vnd.docker.container.image.v1+json')
        old_format.setattr(module, 'LAYER', 'application/vnd.docker.image.rootfs.diff.tar.gzip')
        files = fixture_files()
    with pytest.raises(ValueError, match='Descriptor type'):
        validate(write_archive(tmp_path/'old-format.tar', files))


@pytest.mark.parametrize('change', [
    lambda c: c.update(architecture='arm64'),
    lambda c: c.update(os='windows'),
    lambda c: c['config']['Labels'].update({'org.research.source-state': 'dirty'}),
    lambda c: c['config']['Labels'].update({'org.research.download-contract': 'enabled'}),
    lambda c: c['config']['Labels'].update({'org.opencontainers.image.revision': 'b' * 40}),
    lambda c: c['rootfs'].update(diff_ids=['sha256:' + 'b' * 64]),
])
def test_platform_labels_and_uncompressed_hash_are_required(tmp_path, change):
    archive = write_archive(tmp_path/'bad.tar', fixture_files(config_change=change))
    with pytest.raises(ValueError):
        validate(archive)


@pytest.mark.parametrize('change', [
    lambda i: i['manifests'].append(deepcopy(i['manifests'][0])),
    lambda i: i['manifests'][0]['annotations'].update({'org.opencontainers.image.ref.name': 'latest'}),
    lambda i: i['manifests'][0]['annotations'].update({'io.containerd.image.name': 'elsewhere:latest'}),
    lambda i: i['manifests'][0].update(mediaType=module.INDEX),
    lambda i: i['manifests'][0].update(urls=['https://unreviewed.invalid/image']),
])
def test_one_exact_tagged_manifest_without_external_references(tmp_path, change):
    with pytest.raises(ValueError):
        validate(write_archive(tmp_path/'bad.tar', fixture_files(index_change=change)))


@pytest.mark.parametrize('change', [
    lambda m: m['layers'][0].update(urls=['https://unreviewed.invalid/layer']),
    lambda m: m['layers'][0].update(mediaType='application/vnd.docker.image.rootfs.foreign.diff.tar.gzip'),
    lambda m: m['layers'][0].update(size=m['layers'][0]['size'] + 1),
    lambda m: m['config'].update(size=module.MAX_JSON + 1),
    lambda m: m.update(layers=[]),
])
def test_layer_closure_size_and_media_types_are_closed(tmp_path, change):
    with pytest.raises(ValueError):
        validate(write_archive(tmp_path/'bad.tar', fixture_files(manifest_change=change)))


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'blobs\\escape', 'blobs/sha256/../escape', 'unaccounted.txt'])
def test_archive_paths_cannot_escape_or_hide_unreferenced_files(tmp_path, name):
    with pytest.raises(ValueError):
        validate(write_archive(tmp_path/'bad.tar', fixture_files() + [(name, b'extra')]))
    assert not (tmp_path.parent/'escape').exists()


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME])
def test_links_devices_and_extended_headers_are_rejected_without_extraction(tmp_path, kind):
    item = tarfile.TarInfo('extra'); item.type = kind
    if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
        item.linkname = '../escape'
    with pytest.raises(ValueError):
        validate(write_archive(tmp_path/'bad.tar', fixture_files(), extra=[(item, b'')]))


def test_duplicate_members_corruption_unreferenced_blobs_and_trailing_archive_fail(tmp_path):
    files = fixture_files()
    variants = [files + [files[0]], files[:-1] + [(files[-1][0], b'wrong bytes')],
                files + [('blobs/sha256/' + hashlib.sha256(b'unreferenced').hexdigest(), b'unreferenced')]]
    for index, changed in enumerate(variants):
        with pytest.raises(ValueError):
            validate(write_archive(tmp_path/f'bad{index}.tar', changed))
    path = write_archive(tmp_path/'trailing.tar', files)
    path.write_bytes(path.read_bytes() + b'x' * 512)
    with pytest.raises(ValueError):
        validate(path)


def test_json_duplicate_keys_and_gzip_expansion_cannot_bypass_bounds(tmp_path):
    files = fixture_files()
    files[0] = ('oci-layout', b'{"imageLayoutVersion":"wrong","imageLayoutVersion":"1.0.0"}')
    with pytest.raises(ValueError, match='Duplicate JSON'):
        validate(write_archive(tmp_path/'duplicate.tar', files))
    with pytest.raises(ValueError, match='Uncompressed'):
        validate(write_archive(tmp_path/'expanded.tar', fixture_files()), max_uncompressed_bytes=10)


def test_archive_changes_after_measurement_and_copy_changes_are_rejected(tmp_path):
    files = fixture_files(); path = write_archive(tmp_path/'service.tar', files)
    value = validate(path)
    copy = copy_directory(tmp_path/'copy', files, value)
    (copy/'manifest.json').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed content'):
        module.verify_copy_directory(copy, value)
    path.write_bytes(path.read_bytes()[:-512])
    with pytest.raises(ValueError, match='changed after'):
        module.check_archive(path, value)


def test_pair_storage_charges_full_archives_without_deduplicating_shared_blobs(tmp_path):
    service = write_archive(tmp_path/'service.tar', fixture_files())
    operator = write_archive(tmp_path/'operator.tar', fixture_files('operator'))
    report = module.validate_pair(service, operator, REPOSITORY, COMMIT)
    with pytest.raises(ValueError, match='copy must be qualified'):
        module.storage_projection({}, 0, report)
    report['copy_verified'] = True
    total = service.stat().st_size + operator.stat().st_size
    exact = module.storage_projection({'old1': 3, 'old2': 4}, module.MAX_BYTES - total, report)
    assert exact['projected_bytes'] == module.MAX_BYTES
    assert exact['accounted_existing_bytes'] == module.MAX_BYTES - total
    with pytest.raises(ValueError, match='2 GiB'):
        module.storage_projection({}, module.MAX_BYTES - total + 1, report)
    with pytest.raises(ValueError, match='four-manifest'):
        module.storage_projection({'one': 1, 'two': 1, 'three': 1}, 0, report)
    observed = module.storage_projection({'larger-observed': 100}, 10, report)
    assert observed['projected_bytes'] == 100 + total


def test_pair_keeps_the_independent_uncompressed_limit(tmp_path, monkeypatch):
    service = write_archive(tmp_path/'service.tar', fixture_files())
    operator = write_archive(tmp_path/'operator.tar', fixture_files('operator'))
    # Archive file reading retains its ordinary bound; the shared expansion
    # allowance must reject the second image independently of compression.
    monkeypatch.setattr(module, 'MAX_BYTES', 1000)
    with pytest.raises(ValueError, match='Uncompressed'):
        module.validate_pair(service, operator, REPOSITORY, COMMIT)


@pytest.mark.parametrize('value', [None, [], ['unknown'], ['service', 'service'], ['service', 'operator', 'service'],
                                 'service', {'service': True}, [True]])
def test_component_selection_rejects_empty_unknown_duplicate_or_nonarray(value):
    with pytest.raises(ValueError, match='distinct service/operator'):
        module.selected_components(value)


@pytest.mark.parametrize('selected', [['service'], ['operator']])
def test_one_component_counts_one_manifest_and_charges_its_complete_archive(tmp_path, selected):
    name = selected[0]
    archive = write_archive(tmp_path/(name + '.tar'), fixture_files(name))
    report = module.validate_components({name: archive}, REPOSITORY, COMMIT, selected)
    assert report['schema_version'] == 2 and report['components'] == selected
    assert module.report_components(report) == selected
    report['copy_verified'] = True
    floor = module.MAX_BYTES - archive.stat().st_size
    assert module.storage_projection({'old1': 1, 'old2': 2, 'old3': 3}, floor, report)['projected_bytes'] == module.MAX_BYTES
    with pytest.raises(ValueError, match='2 GiB'):
        module.storage_projection({}, floor + 1, report)
    with pytest.raises(ValueError, match='four-manifest'):
        module.storage_projection(dict.fromkeys(('old1', 'old2', 'old3', 'old4'), 1), 0, report)
    with pytest.raises(ValueError, match='paths differ'):
        module.validate_components({'service': archive, 'operator': archive}, REPOSITORY, COMMIT, selected)
    report['artifacts']['unreviewed'] = report['artifacts'][name]
    with pytest.raises(ValueError, match='artifacts differ'):
        module.storage_projection({}, 0, report)


def workflow_script(name):
    workflow = Path(__file__).resolve().parents[3]/'.github/workflows/research-image-build.yml'
    step = workflow.read_text().split('      - name: ' + name + '\n', 1)[1].split('\n      - name:', 1)[0]
    embedded = re.search(r"          python - <<'PY'\n(.*?)          PY", step, re.S).group(1)
    return '\n'.join(line.removeprefix('          ') for line in embedded.splitlines())


@pytest.mark.parametrize('attempt', [None, '0', '2', '100'])
def test_workflow_rerun_is_rejected_before_source_or_request_access(monkeypatch, attempt):
    script = workflow_script('Validate destination and clean source')
    if attempt is None:
        monkeypatch.delenv('GITHUB_RUN_ATTEMPT', raising=False)
    else:
        monkeypatch.setenv('GITHUB_RUN_ATTEMPT', attempt)
    monkeypatch.delenv('GITHUB_EVENT_NAME', raising=False)
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: pytest.fail('No build or Git command may run'))
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: pytest.fail('No request or artifact may be read'))
    with pytest.raises(SystemExit, match='Build reruns are blocked'):
        exec(compile(script, 'workflow-destination', 'exec'), {})


@pytest.mark.parametrize('components', ['service,operator', 'service', '', 'unknown', 'service,service'])
def test_workflow_first_attempt_accepts_only_reviewed_destination_components(tmp_path, monkeypatch, components):
    script = workflow_script('Validate destination and clean source')
    environment = dict(GITHUB_RUN_ATTEMPT='1', GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_RUN_ID='fixture-run',
        GITHUB_SHA=COMMIT, GITHUB_OUTPUT=str(tmp_path/'output'), BUILD_PROJECT='research-fixture',
        BUILD_REGION='asia-southeast1', BUILD_ENVIRONMENT='staging', BUILD_RETAINED_STORAGE_BYTES='923179605',
        BUILD_COMPONENTS=components,
        BUILD_SERVICE_ACCOUNT='research-staging-build@research-fixture.iam.gserviceaccount.com',
        BUILD_WIF_PROVIDER='projects/12345/locations/global/workloadIdentityPools/research-staging-build/providers/github')
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subprocess, 'check_output', lambda command, **kwargs: COMMIT if command[1] == 'rev-parse' else '')
    if components not in ('service,operator', 'service'):
        with pytest.raises(ValueError, match='distinct service/operator'):
            exec(compile(script, 'workflow-destination', 'exec'), {})
        assert not (tmp_path/'output').exists()
        return
    exec(compile(script, 'workflow-destination', 'exec'), {})
    output = (tmp_path/'output').read_text()
    assert 'enabled=true\n' in output and 'retained_storage_bytes=923179605\n' in output
    assert 'components=' + json.dumps(components.split(','), separators=(',', ':')) + '\n' in output


@pytest.mark.parametrize('components', [None, [], ['invalid'], ['service', 'service'], ['service'], ['operator'], ['service', 'operator']])
def test_push_request_component_allowlist_before_build(tmp_path, monkeypatch, components):
    request = dict(project_id='research-fixture', region='asia-southeast1', environment='staging', request_id='reviewed',
                   retained_storage_bytes='1858163285', components=components)
    directory = tmp_path/'.github'; directory.mkdir()
    (directory/'research-image-build-request.json').write_text(json.dumps(request))
    monkeypatch.chdir(tmp_path)
    for key, value in dict(GITHUB_RUN_ATTEMPT='1', GITHUB_EVENT_NAME='push', GITHUB_SHA=COMMIT,
        GITHUB_OUTPUT=str(tmp_path/'output'), BUILD_SERVICE_ACCOUNT='research-staging-build@research-fixture.iam.gserviceaccount.com',
        BUILD_WIF_PROVIDER='projects/12345/locations/global/workloadIdentityPools/research-staging-build/providers/github').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subprocess, 'check_output', lambda command, **kwargs: COMMIT if command[1] == 'rev-parse' else '')
    script = workflow_script('Validate destination and clean source')
    if components in (['service'], ['operator'], ['service', 'operator']):
        exec(compile(script, 'workflow-destination', 'exec'), {})
        assert 'components=' + json.dumps(components, separators=(',', ':')) + '\n' in (tmp_path/'output').read_text()
    else:
        with pytest.raises(ValueError, match='distinct service/operator'):
            exec(compile(script, 'workflow-destination', 'exec'), {})
        assert not (tmp_path/'output').exists()


@pytest.fixture
def workflow_upload(tmp_path, monkeypatch):
    paths = {kind: write_archive(tmp_path/('research-' + kind + '.oci.tar'), fixture_files(kind))
             for kind in ('service', 'operator')}
    report = module.validate_pair(paths['service'], paths['operator'], REPOSITORY, COMMIT)
    report['copy_verified'] = True
    report['registry_projection'] = module.storage_projection({}, 923179605, report)
    (tmp_path/'image-artifacts.json').write_text(json.dumps(report))
    monkeypatch.chdir(tmp_path)
    for key, value in dict(IMAGE_REPOSITORY=REPOSITORY, GITHUB_SHA=COMMIT,
                           RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(tmp_path/'outputs'),
                           SELECTED_COMPONENTS='["service","operator"]').items():
        monkeypatch.setenv(key, value)
    return tmp_path, paths, report, workflow_script('Push the qualified images and resolve their immutable digests')


def test_workflow_copies_exact_qualified_archives_and_records_intent_before_each_copy(workflow_upload, monkeypatch):
    root, paths, report, script = workflow_upload
    calls = []
    def copied(command, **options):
        kind = 'service' if command[-1].endswith('/service:' + COMMIT) else 'operator'
        saved = json.loads((root/'image-artifacts.json').read_text())
        assert saved['upload_intents'][kind]['image'] == report['artifacts'][kind]['image']
        assert command[:6] == ['skopeo', 'copy', '--preserve-digests', '--retry-times', '0', '--dest-authfile']
        assert command[-2] == 'oci-archive:' + str(paths[kind]) + ':' + COMMIT
        assert command[-1] == 'docker://' + REPOSITORY + '/' + kind + ':' + COMMIT
        assert options == {'check': True, 'timeout': 300}
        Path(command[command.index('--digestfile') + 1]).write_text(report['artifacts'][kind]['manifest_digest'])
        calls.append(kind)
    monkeypatch.setattr(subprocess, 'run', copied)
    exec(compile(script, 'workflow-push', 'exec'), {})
    assert calls == ['service', 'operator']
    saved = json.loads((root/'image-artifacts.json').read_text())
    assert all(saved['upload_intents'][kind]['verified_digest'] == report['artifacts'][kind]['manifest_digest'] for kind in calls)
    with pytest.raises(ValueError, match='previous intent'):
        exec(compile(script, 'workflow-push', 'exec'), {})
    assert calls == ['service', 'operator']


@pytest.mark.parametrize('failure', ['lost_ack', 'wrong_digest'])
def test_workflow_uncertain_first_upload_blocks_second_and_repetition(workflow_upload, monkeypatch, failure):
    root, _, _, script = workflow_upload
    calls = []
    def failed(command, **options):
        calls.append(command)
        if failure == 'lost_ack':
            raise subprocess.CalledProcessError(1, ['skopeo'])
        Path(command[command.index('--digestfile') + 1]).write_text('sha256:' + 'f' * 64)
    monkeypatch.setattr(subprocess, 'run', failed)
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        exec(compile(script, 'workflow-push', 'exec'), {})
    assert len(calls) == 1
    saved = json.loads((root/'image-artifacts.json').read_text())
    assert set(saved['upload_intents']) == {'service'} and 'verified_digest' not in saved['upload_intents']['service']
    with pytest.raises(ValueError, match='previous intent'):
        exec(compile(script, 'workflow-push', 'exec'), {})
    assert len(calls) == 1


def test_workflow_checks_both_archives_before_first_upload(workflow_upload, monkeypatch):
    _, paths, _, script = workflow_upload
    paths['operator'].write_bytes(b'changed')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('No upload may run'))
    with pytest.raises(ValueError, match='changed after'):
        exec(compile(script, 'workflow-push', 'exec'), {})


def select_service(workflow_upload, monkeypatch):
    root, paths, _, _ = workflow_upload
    report = module.validate_components({'service': paths['service']}, REPOSITORY, COMMIT, ['service'])
    report['copy_verified'] = True
    report['registry_projection'] = module.storage_projection({'old1': 1, 'old2': 2, 'old3': 3}, 1858163285, report)
    (root/'image-artifacts.json').write_text(json.dumps(report))
    monkeypatch.setenv('SELECTED_COMPONENTS', '["service"]')
    return report


def test_service_only_upload_and_final_report_never_include_unreviewed_operator(workflow_upload, monkeypatch):
    root, paths, _, script = workflow_upload
    report = select_service(workflow_upload, monkeypatch)
    paths['operator'].write_bytes(b'unreviewed, invalid archive must never be opened or uploaded')
    calls = []
    def copied(command, **options):
        assert command[-1] == 'docker://' + REPOSITORY + '/service:' + COMMIT
        saved = json.loads((root/'image-artifacts.json').read_text())
        assert set(saved['upload_intents']) == {'service'}
        Path(command[command.index('--digestfile') + 1]).write_text(report['artifacts']['service']['manifest_digest'])
        calls.append(command)
    monkeypatch.setattr(subprocess, 'run', copied)
    exec(compile(script, 'workflow-push', 'exec'), {})
    assert len(calls) == 1
    saved = json.loads((root/'image-artifacts.json').read_text())
    saved['skopeo_version'] = 'fixture qualified local copy'
    (root/'image-artifacts.json').write_text(json.dumps(saved))
    for key, value in dict(BUILD_REQUEST_ID='service-only-reviewed', SERVICE_DIGEST=report['artifacts']['service']['manifest_digest'],
        OPERATOR_DIGEST='', EXISTING_IMAGE_BYTES='6', EXISTING_IMAGE_COUNT='3', RETAINED_STORAGE_BYTES='1858163285',
        ACCOUNTED_EXISTING_BYTES='1858163285', PROJECTED_IMAGE_BYTES=str(report['registry_projection']['projected_bytes']),
        GITHUB_RUN_ID='new-reviewed-run', GITHUB_RUN_ATTEMPT='1', GITHUB_STEP_SUMMARY=str(root/'summary')).items():
        monkeypatch.setenv(key, value)
    record = workflow_script('Record immutable image references')
    exec(compile(record, 'workflow-record', 'exec'), {})
    final = json.loads((root/'image-digests.json').read_text())
    assert final['schema_version'] == 3 and final['components'] == ['service']
    assert all(set(final[key]) == {'service'} for key in ('images', 'archive_artifacts', 'compressed_archive_bytes',
                                                       'uncompressed_image_bytes', 'archive_sha256'))
    assert final['existing_registry_image_count'] == 3
    with pytest.raises(ValueError, match='previous intent'):
        exec(compile(script, 'workflow-push', 'exec'), {})
    assert len(calls) == 1


def test_unreviewed_component_cannot_be_injected_into_upload_report(workflow_upload, monkeypatch):
    _, _, _, script = workflow_upload
    monkeypatch.setenv('SELECTED_COMPONENTS', '["service"]')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('Unreviewed component cannot upload'))
    with pytest.raises(ValueError, match='selection differs'):
        exec(compile(script, 'workflow-push', 'exec'), {})


@pytest.mark.parametrize('failure', ['lost_ack', 'wrong_digest'])
def test_service_only_uncertain_upload_is_never_reissued(workflow_upload, monkeypatch, failure):
    root, _, _, script = workflow_upload
    select_service(workflow_upload, monkeypatch)
    calls = []
    def failed(command, **options):
        calls.append(command)
        if failure == 'lost_ack':
            raise subprocess.CalledProcessError(1, ['skopeo'])
        Path(command[command.index('--digestfile') + 1]).write_text('sha256:' + 'f' * 64)
    monkeypatch.setattr(subprocess, 'run', failed)
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        exec(compile(script, 'workflow-push', 'exec'), {})
    saved = json.loads((root/'image-artifacts.json').read_text())
    assert set(saved['upload_intents']) == {'service'} and 'verified_digest' not in saved['upload_intents']['service']
    with pytest.raises(ValueError, match='previous intent'):
        exec(compile(script, 'workflow-push', 'exec'), {})
    assert len(calls) == 1


@pytest.mark.parametrize('selected', [['service'], ['service', 'operator']])
def test_workflow_local_copy_checks_only_selected_exact_blobs_before_qualification(workflow_upload, monkeypatch, selected):
    root, _, pair, _ = workflow_upload
    report = select_service(workflow_upload, monkeypatch) if selected == ['service'] else pair
    report['copy_verified'] = False
    (root/'image-artifacts.json').write_text(json.dumps(report))
    calls = []
    def copied(command, **options):
        name = 'service' if command[-1].endswith('research-service-copy') else 'operator'
        assert name in selected
        assert command[:5] == ['skopeo', 'copy', '--preserve-digests', '--retry-times', '0']
        copy_directory(Path(command[-1].removeprefix('dir:')), fixture_files(name), report['artifacts'][name])
        assert json.loads((root/'image-artifacts.json').read_text())['copy_verified'] is False
        calls.append(name)
    monkeypatch.setattr(subprocess, 'run', copied)
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'fixture version')
    script = workflow_script('Prove digest-preserving local copy compatibility')
    exec(compile(script, 'workflow-local-copy', 'exec'), {})
    assert calls == selected
    assert json.loads((root/'image-artifacts.json').read_text())['copy_verified'] is True


def test_default_pair_final_report_retains_legacy_schema_and_exact_components(workflow_upload, monkeypatch):
    root, _, report, _ = workflow_upload
    report['skopeo_version'] = 'fixture qualified local copy'
    report['upload_intents'] = {name: {'verified_digest': item['manifest_digest']} for name, item in report['artifacts'].items()}
    (root/'image-artifacts.json').write_text(json.dumps(report))
    for key, value in dict(BUILD_REQUEST_ID='pair-reviewed', SERVICE_DIGEST=report['artifacts']['service']['manifest_digest'],
        OPERATOR_DIGEST=report['artifacts']['operator']['manifest_digest'], EXISTING_IMAGE_BYTES='0', EXISTING_IMAGE_COUNT='0',
        RETAINED_STORAGE_BYTES='923179605', ACCOUNTED_EXISTING_BYTES='923179605',
        PROJECTED_IMAGE_BYTES=str(report['registry_projection']['projected_bytes']),
        GITHUB_RUN_ID='pair-reviewed-run', GITHUB_RUN_ATTEMPT='1', GITHUB_STEP_SUMMARY=str(root/'summary')).items():
        monkeypatch.setenv(key, value)
    script = workflow_script('Record immutable image references')
    exec(compile(script, 'workflow-record', 'exec'), {})
    final = json.loads((root/'image-digests.json').read_text())
    assert final['schema_version'] == 2 and 'components' not in final
    assert set(final['images']) == {'service', 'operator'}
    assert report['schema_version'] == 1 and 'components' not in report
    report['upload_intents']['operator'].pop('verified_digest')
    (root/'image-artifacts.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='digest differs'):
        exec(compile(script, 'workflow-record', 'exec'), {})
