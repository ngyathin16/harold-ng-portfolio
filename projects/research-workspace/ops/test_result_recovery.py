"""Recovery verifies logical records and fences deletion against real readers."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import threading
from unittest.mock import Mock, patch

import psycopg
from psycopg.rows import dict_row
import pytest

from ops import backup, result_recovery
from ops.test_backup import Store
from service.result_objects import ResultObjects
from service.result_store import ResultReader, shared_lock, snapshot
from service.test_service import service, submission
from service.worker import claim, execute
from service.budget import Budget, reserve_search, search_liability
from service.test_budget import ledger


SYSTEM_ENV = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE') if name in os.environ}


def logical_row(identity, champion='team', **changes):
    return dict(run_id='run', configuration_key=identity, champion_set_key=champion,
                active_trait_count=2, total_cost=None, champion_names='Names',
                discovery_index=7, result={'configuration_key': identity, 'cost': None}, **changes)


def test_v2_checks_ignore_storage_order_preserve_duplicates_and_detect_payload_change(tmp_path):
    rows = [logical_row(str(index), str(index % 2)) for index in range(9)]
    with patch.object(result_recovery, 'HASH_CHUNK_ROWS', 2):
        expected = result_recovery.logical_check(iter(rows), tmp_path)
        assert result_recovery.logical_check(reversed(rows), tmp_path) == expected
        duplicate = result_recovery.logical_check(iter(rows + [rows[0]]), tmp_path)
        changed = [dict(row) for row in rows]
        changed[0]['result'] = {'configuration_key': '0', 'cost': 0}
        different = result_recovery.logical_check(iter(changed), tmp_path)
    assert expected['stored_rows'] == 9 and expected['stored_champion_sets'] == 2
    assert duplicate['stored_rows'] == 10 and duplicate['rows_sha256'] != expected['rows_sha256']
    assert different['rows_sha256'] != expected['rows_sha256']
    assert list(tmp_path.iterdir()) == []


def test_v2_field_framing_distinguishes_concatenated_values():
    first, second = logical_row('ab', 'c'), logical_row('a', 'bc')
    assert result_recovery.row_digest(first) != result_recovery.row_digest(second)


def test_digest_limit_fails_without_leaving_scratch(tmp_path):
    with patch.object(result_recovery, 'MAX_ROWS', 1), pytest.raises(ValueError, match='allowance'):
        result_recovery.logical_check(iter([logical_row('a'), logical_row('b')]), tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('prefix', ['', 'OBJECT_'])
def test_operator_json_fallback_uses_primary_backup_bucket_and_is_scoped(prefix):
    credentials = dict(bucket='private-backup', allowed_buckets=['private-backup', 'private-results'],
        endpoint='https://test-account.r2.cloudflarestorage.com', access_key_id='access', secret_access_key='secret')
    with patch.dict(os.environ, {**SYSTEM_ENV, 'RESEARCH_RESULT_CREDENTIALS_JSON': json.dumps(credentials)}, clear=True):
        store = backup.R2Store(prefix, 'isolated', budget=Mock())
        try:
            assert store.bucket == 'private-backup' and store.prefix == 'isolated'
            assert store.endpoint == credentials['endpoint']
        finally:
            store.client.close()


@pytest.mark.parametrize('prefix', ['', 'OBJECT_'])
def test_partial_legacy_configuration_never_combines_with_operator_credentials(prefix):
    with patch.dict(os.environ, {prefix + 'R2_BUCKET': 'legacy', 'RESEARCH_RESULT_CREDENTIALS_JSON': '{}'}, clear=True):
        with pytest.raises(ValueError, match='cannot be combined'):
            backup.R2Store(prefix)


def test_complete_legacy_configuration_retains_precedence():
    env = dict(R2_ENDPOINT='https://legacy.r2.cloudflarestorage.com', R2_BUCKET='legacy',
               R2_ACCESS_KEY_ID='legacy-access', R2_SECRET_ACCESS_KEY='legacy-secret',
               RESEARCH_RESULT_CREDENTIALS_JSON='invalid-unused-json')
    with patch.dict(os.environ, {**SYSTEM_ENV, **env}, clear=True):
        store = backup.R2Store()
        try:
            assert store.bucket == 'legacy'
        finally:
            store.client.close()


@pytest.fixture
def completed(service, tmp_path):
    client, pool, settings, publication, _ = service
    settings = replace(settings, result_storage_mode='objects', result_local_root=str(tmp_path / 'live'),
                       result_scratch_root=str(tmp_path / 'scratch'))
    accepted = submission(client, publication)
    run = claim(pool, settings)
    assert run['id'] == accepted['query_id']
    assert execute(pool, settings, run) == 'complete'
    objects = ResultObjects.from_settings(settings)
    try:
        yield pool, settings, run['id'], objects
    finally:
        objects.close()


def expire(pool, run_id):
    with pool.connection() as connection:
        connection.execute("UPDATE research.query_runs SET expires_at=now()-interval '1 day' WHERE id=%s", (run_id,))


def collect(settings, objects, run_id):
    with backup.database_operation_lock(settings.database_url) as connection:
        return result_recovery.cleanup(connection, objects, run_ids=[run_id])


def test_self_contained_results_recount_when_legacy_table_is_empty(completed, tmp_path):
    pool, settings, run_id, objects = completed
    restored = tmp_path / 'restored'
    restored.mkdir()
    with pool.connection() as connection:
        assert connection.execute('SELECT count(*) AS n FROM research.query_results WHERE run_id=%s', (run_id,)).fetchone()['n'] == 0
        before = next(item for item in backup.query_checks(connection, objects) if item['id'] == run_id)
        refs = result_recovery.inventory(connection)
        source = result_recovery.SourceStore(objects, refs, connection)
        for ref in refs:
            backup.check_stream(source, ref.key, ref.to_dict(), restored / 'results' / ref.key)
    staged = result_recovery.staged_objects(restored)
    with pool.connection() as connection:
        after = next(item for item in backup.query_checks(connection, staged) if item['id'] == run_id)
    assert before == after and before['stored_rows'] == 3
    victim = restored / 'results' / refs[0].key
    victim.write_bytes(b'x' * victim.stat().st_size)
    with pool.connection() as connection, pytest.raises(ValueError):
        backup.query_checks(connection, staged)


def test_backup_manifest_contains_independent_dictionary_and_packs(completed, tmp_path):
    pool, settings, run_id, objects = completed
    staging = tmp_path / 'backup'

    def pg_command(tool, url, arguments, **kwargs):
        if tool == 'pg_dump' and '--version' not in arguments:
            Path(next(item[7:] for item in arguments if item.startswith('--file='))).write_bytes(b'fixture dump')
        return 'fixture PostgreSQL client'

    with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row) as connection, \
            patch.dict(os.environ, {'RESEARCH_APP_COMMIT': 'a' * 40}), \
            patch.object(backup, 'pg_command', side_effect=pg_command):
        manifest = backup.prepare_set(settings.database_url, None, staging, 'set1',
                                      connection=connection, result_objects=objects)
    assert manifest['format_version'] == 2 and manifest['query_check_version'] == 2
    assert len([item for item in manifest['members'] if item['path'].startswith('results/')]) == 4
    expected = next(item for item in manifest['query_checks'] if item['id'] == run_id)
    assert expected['stored_rows'] == 3
    archive = Store()
    backup.publish_set(archive, staging, manifest)
    with pool.connection() as connection:
        refs = result_recovery.inventory(connection)
    for ref in refs:
        objects.delete(ref)
    restored = tmp_path / 'retrieved'
    backup.verify_set(archive, 'set1', restored)
    with pool.connection() as connection:
        actual = next(item for item in backup.query_checks(connection, result_recovery.staged_objects(restored)) if item['id'] == run_id)
    assert actual == expected


def test_v2_manifest_cannot_fall_back_to_empty_legacy_checksum(tmp_path):
    staging = tmp_path / 'set1'
    staging.mkdir()
    (staging / 'database.dump').write_bytes(b'dump')
    (staging / 'roles.json').write_bytes(b'{}')
    manifest = {'format_version': 2, 'backup_id': 'set1', 'members': [
        dict(path=path.name, size=path.stat().st_size, sha256=backup.digest_file(path)) for path in staging.iterdir()]}
    store = Store()
    with pytest.raises(ValueError, match='canonical logical'):
        backup.publish_set(store, staging, manifest)
    assert store.objects == {}


def test_partial_checkpoint_recounts_sql_tail_with_only_dictionary_object(service, tmp_path):
    client, pool, settings, publication, _ = service
    settings = replace(settings, result_storage_mode='objects', result_local_root=str(tmp_path / 'live'),
                       result_scratch_root=str(tmp_path / 'scratch'), max_results=1)
    accepted = submission(client, publication)
    run = claim(pool, settings)
    assert run['id'] == accepted['query_id']
    assert execute(pool, settings, run) == 'resource_limited'
    objects = ResultObjects.from_settings(settings)
    restored = tmp_path / 'restored'
    with pool.connection() as connection:
        refs = result_recovery.inventory(connection)
        assert len(refs) == 1 and refs[0].key.endswith('/dictionary.json')
        state = snapshot(connection, run['id'])
        assert state['complete_orders'] is None and len(state['result_tail']) == 25
        source = result_recovery.SourceStore(objects, refs, connection)
        ref = refs[0]
        backup.check_stream(source, ref.key, ref.to_dict(), restored / 'results' / ref.key)
        checked = next(item for item in backup.query_checks(connection, result_recovery.staged_objects(restored)) if item['id'] == run['id'])
    assert checked['stored_rows'] == 1 and checked['state'] == 'resource_limited'


def test_gc_waits_for_reader_and_new_reader_observes_detached_generation(completed):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    started, finished = threading.Event(), threading.Event()
    errors, reports = [], []

    def gc():
        started.set()
        try:
            reports.append(collect(settings, objects, run_id))
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    with psycopg.connect(settings.database_url, row_factory=dict_row) as reader:
        shared_lock(reader, run_id)
        before = snapshot(reader, run_id)
        thread = threading.Thread(target=gc)
        thread.start()
        assert started.wait(2) and not finished.wait(0.15)
        assert len(ResultReader(objects).page(reader, before, 'active_traits', 0, 200)) == 3
    thread.join(10)
    assert finished.is_set() and not errors
    assert reports[0]['deleted_result_generations'] == 1
    with pool.connection() as connection:
        shared_lock(connection, run_id)
        assert snapshot(connection, run_id)['result_generation_id'] is None


def test_reader_waiting_for_gc_takes_fresh_read_committed_snapshot(completed):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    deleting, release, read_started, read_finished = (threading.Event() for _ in range(4))
    original = objects.delete
    states, errors = [], []

    def paused_delete(ref):
        deleting.set()
        if not release.wait(5):
            raise TimeoutError('Test GC was not released')
        original(ref)

    def gc():
        try:
            collect(settings, objects, run_id)
        except Exception as error:
            errors.append(error)

    def read():
        try:
            with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
                read_started.set()
                shared_lock(connection, run_id)
                states.append(snapshot(connection, run_id))
        except Exception as error:
            errors.append(error)
        finally:
            read_finished.set()

    with patch.object(objects, 'delete', side_effect=paused_delete):
        collector = threading.Thread(target=gc)
        collector.start()
        assert deleting.wait(3)
        reader = threading.Thread(target=read)
        reader.start()
        assert read_started.wait(2) and not read_finished.wait(0.15)
        release.set()
        collector.join(10)
        reader.join(10)
    assert not errors and read_finished.is_set()
    assert states[0]['result_generation_id'] is None


@pytest.mark.parametrize('state', ['uncertain', 'launching', 'running'])
def test_unconfirmed_platform_execution_blocks_gc(completed, state):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    with pool.connection() as connection:
        connection.execute('INSERT INTO research.execution_reservations(id,run_id,generation,state) VALUES(%s,%s,999,%s)',
                           ('ops-' + run_id, run_id, state))
    assert collect(settings, objects, run_id)['deleted_result_generations'] == 0
    with pool.connection() as connection:
        assert snapshot(connection, run_id)['result_generation_id'] is not None


def test_expired_worker_lease_does_not_authorize_gc(completed):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    with pool.connection() as connection:
        connection.execute("UPDATE research.query_runs SET state='running',lease_until=now()-interval '1 day' WHERE id=%s", (run_id,))
    assert collect(settings, objects, run_id)['deleted_result_generations'] == 0


def test_interrupted_gc_keeps_intents_and_can_resume(completed):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    original, calls = objects.delete, []

    def fail_after_delete(ref):
        original(ref)
        calls.append(ref)
        if len(calls) == 2:
            raise OSError('Lost cleanup acknowledgement')

    with patch.object(objects, 'delete', side_effect=fail_after_delete), pytest.raises(OSError):
        collect(settings, objects, run_id)
    with pool.connection() as connection:
        generation = snapshot(connection, run_id)['result_generation_id']
        assert generation is not None
        assert connection.execute('SELECT count(*) AS n FROM research.result_object_intents WHERE generation_id=%s', (generation,)).fetchone()['n'] == 4
    assert collect(settings, objects, run_id)['deleted_result_generations'] == 1
    assert all(not objects.exists(ref.key) for ref in calls)


def test_missing_planned_upload_stays_charged_until_delayed_put_is_verified(completed):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    body = b'delayed immutable result bytes'
    with pool.connection() as connection:
        generation = snapshot(connection, run_id)['result_generation_id']
        key = generation + '/segment-999999.bin'
        connection.execute("UPDATE research.query_runs SET state='failed' WHERE id=%s", (run_id,))
        connection.execute('INSERT INTO research.result_object_intents(generation_id,key,size,sha256) VALUES(%s,%s,%s,%s)',
                           (generation, key, len(body), hashlib.sha256(body).hexdigest()))

    class DelayedPut:
        def put(self, key, payload):
            self.pending = key, payload
            raise OSError('Transport returned before the remote PUT finished')

        def finish(self):
            objects.put(*self.pending)

    delayed = DelayedPut()
    with pytest.raises(OSError):
        delayed.put(key, body)
    report = collect(settings, objects, run_id)
    assert report['deleted_result_generations'] == 0 and report['deferred_result_generations'] == 1
    with pool.connection() as connection:
        assert snapshot(connection, run_id)['result_generation_id'] == generation
        assert connection.execute('SELECT size,state FROM research.result_object_intents WHERE key=%s', (key,)).fetchone() == {'size': len(body), 'state': 'planned'}
        assert connection.execute('SELECT count(*) AS n FROM research.result_object_intents WHERE generation_id=%s', (generation,)).fetchone()['n'] == 5
    delayed.finish()
    with patch.object(objects, 'verify', wraps=objects.verify) as verify:
        report = collect(settings, objects, run_id)
    assert verify.call_count == 1
    assert report['deferred_result_generations'] == 1 and report['deleted_result_generations'] == 0
    assert objects.exists(key)
    with pool.connection() as connection:
        assert connection.execute('SELECT state FROM research.result_object_intents WHERE key=%s', (key,)).fetchone()['state'] == 'verified'
    report = collect(settings, objects, run_id)
    assert report['deleted_result_generations'] == 1 and not objects.exists(key)


def test_verified_planned_intent_survives_later_gc_rollback(completed):
    pool, settings, run_id, objects = completed
    expire(pool, run_id)
    body = b'completed upload whose verification acknowledgement was lost'
    with pool.connection() as connection:
        generation = snapshot(connection, run_id)['result_generation_id']
        key = generation + '/segment-999999.bin'
        connection.execute('INSERT INTO research.result_object_intents(generation_id,key,size,sha256) VALUES(%s,%s,%s,%s)',
                           (generation, key, len(body), hashlib.sha256(body).hexdigest()))
    objects.put(key, body)
    assert collect(settings, objects, run_id)['deferred_result_generations'] == 1
    original = objects.delete

    def lose_deletion_ack(ref):
        original(ref)
        if ref.key == key:
            raise OSError('Cleanup transaction rolled back after deletion')

    with patch.object(objects, 'delete', side_effect=lose_deletion_ack), pytest.raises(OSError):
        collect(settings, objects, run_id)
    assert not objects.exists(key)
    with pool.connection() as connection:
        assert connection.execute('SELECT state FROM research.result_object_intents WHERE key=%s', (key,)).fetchone()['state'] == 'verified'
    report = collect(settings, objects, run_id)
    assert report['deleted_result_generations'] == 1 and report['deferred_result_generations'] == 0


def test_gc_refunds_expired_run_that_never_created_a_result_generation(service, ledger):
    client, pool, settings, publication, _ = service
    budget_pool, window = ledger
    run_id = submission(client, publication)['query_id']
    with pool.connection() as connection:
        connection.execute('UPDATE research.query_runs SET dispatch_generation=1 WHERE id=%s', (run_id,))
        reserve_search(connection, window, run_id, 1, 3)
        connection.execute("UPDATE research.query_runs SET state='failed',expires_at=now()-interval '1 day' WHERE id=%s", (run_id,))
        assert connection.execute('SELECT 1 FROM research.result_generations WHERE run_id=%s', (run_id,)).fetchone() is None
    objects = Mock(spec=ResultObjects)
    with backup.database_operation_lock(settings.database_url) as connection:
        report = result_recovery.cleanup(connection, objects, run_ids=[run_id], budget=Budget(budget_pool, window))
    assert report['deleted_result_generations'] == report['deleted_result_objects'] == 0
    assert objects.mock_calls == []
    with pool.connection() as connection:
        assert connection.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100
        assert connection.execute('SELECT released FROM research.budget_search_receipts WHERE run_id=%s', (run_id,)).fetchone()['released'] is True
        assert connection.execute("SELECT used FROM research.budget_metrics WHERE metric='search_attempts'").fetchone()['used'] == 3


def test_gc_refunds_run_reservation_only_after_every_object_deletion_is_proven(service, ledger, tmp_path):
    client, pool, settings, publication, _ = service
    budget_pool, window = ledger
    settings = replace(settings, result_storage_mode='objects', result_local_root=str(tmp_path / 'live'),
                       result_scratch_root=str(tmp_path / 'scratch'))
    accepted = submission(client, publication)
    run_id = accepted['query_id']
    with pool.connection() as connection:
        connection.execute('UPDATE research.query_runs SET dispatch_generation=1 WHERE id=%s', (run_id,))
        reserve_search(connection, window, run_id, 1, 3)
    run = claim(pool, settings)
    assert run['id'] == run_id and execute(pool, settings, run) == 'complete'
    objects = ResultObjects.from_settings(settings)
    budget = Budget(budget_pool, window)
    expire(pool, run_id)
    try:
        with patch.object(objects, 'delete', side_effect=OSError('unproven deletion')), pytest.raises(OSError):
            with backup.database_operation_lock(settings.database_url) as connection:
                result_recovery.cleanup(connection, objects, run_ids=[run_id], budget=budget)
        with pool.connection() as connection:
            assert connection.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100 + search_liability(3)
            assert connection.execute('SELECT released FROM research.budget_search_receipts').fetchone()['released'] is False
        with backup.database_operation_lock(settings.database_url) as connection:
            report = result_recovery.cleanup(connection, objects, run_ids=[run_id], budget=budget)
        assert report['deleted_result_generations'] == 1
        with pool.connection() as connection:
            assert connection.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100
            assert connection.execute('SELECT released FROM research.budget_search_receipts').fetchone()['released'] is True
            assert connection.execute("SELECT used FROM research.budget_metrics WHERE metric='search_attempts'").fetchone()['used'] == 3
    finally:
        objects.close()
