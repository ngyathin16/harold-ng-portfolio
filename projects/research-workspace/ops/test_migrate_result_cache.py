"""Migration parity and crash recovery against two disposable PostgreSQL databases."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import combinations, islice
import json
import os
from pathlib import Path
from unittest.mock import patch
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from ops import migrate_result_cache as migration, result_recovery
from service.budget import BudgetUnavailable, METRICS, MIGRATION_COPIES, install_window
from service.result_objects import ResultObjects
from service.result_store import ResultReader, snapshot
from service.test_result_codec import payload, rules_fixture


TARGET = dict(host='target.example', port=5432, dbname='research')
RESOURCES = dict(project='research-project', api_service='research-api', dispatcher_service='research-dispatcher',
                 worker_job='research-worker', maintenance_job='research-maintenance')


def report_fixture(now):
    return dict(checked_at=now.isoformat(), target=TARGET, resources=RESOURCES, environment_id=None,
                checks=dict(api_writers_disabled=True, dispatcher_disabled=True, maintenance_writers_disabled=True,
                            outstanding_worker_executions=0, outstanding_maintenance_executions=0))


@pytest.mark.parametrize('mutation', [
    lambda item: item.update(checked_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()),
    lambda item: item.update(checked_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()),
    lambda item: item.update(checked_at='2026-01-01T00:00:00'),
    lambda item: item['target'].update(host='other.example'),
    lambda item: item['target'].update(port='5432'),
    lambda item: item['checks'].update(api_writers_disabled=1),
    lambda item: item['checks'].update(dispatcher_disabled=False),
    lambda item: item['checks'].update(maintenance_writers_disabled=False),
    lambda item: item['checks'].update(outstanding_worker_executions=False),
    lambda item: item['checks'].update(outstanding_maintenance_executions=1),
    lambda item: item['resources'].update(worker_job='wrong-job'),
    lambda item: item.update(environment_id='wrong-environment'),
])
def test_quiescence_evidence_is_recent_complete_and_bound_to_target(tmp_path, mutation):
    now = datetime.now(timezone.utc)
    value = deepcopy(report_fixture(now))
    mutation(value)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(value))
    with pytest.raises(migration.MigrationBlocked, match='Quiescence report'):
        migration.quiescence_report(path, TARGET, RESOURCES, now=now)


def test_quiescence_report_is_reread_and_size_bounded(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report_fixture(now)))
    assert migration.quiescence_report(path, TARGET, RESOURCES, now=now)
    path.write_bytes(b' ' * 65537)
    with pytest.raises(migration.MigrationBlocked):
        migration.quiescence_report(path, TARGET, RESOURCES, now=now)


INSERT_RESULT = '''INSERT INTO research.query_results(run_id,configuration_key,champion_set_key,
    active_trait_count,total_cost,champion_names,discovery_index,result) OVERRIDING SYSTEM VALUE
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)'''


def write_results(connection, run_id, values, ordinals):
    with connection.cursor() as cursor:
        cursor.executemany(INSERT_RESULT, [(run_id, value['configuration_key'], value['champion_set_key'],
            value['active_trait_count'], value['total_cost'], value['names_sort'], ordinal, Jsonb(value))
            for ordinal, value in zip(ordinals, values)])


@pytest.fixture
def databases(tmp_path):
    source_url = os.environ.get('RESEARCH_MIGRATION_SOURCE_TEST_URL')
    target_url = os.environ.get('RESEARCH_MIGRATION_TARGET_TEST_URL')
    if not source_url or not target_url:
        pytest.skip('Set both migration test URLs to separate migrated disposable PostgreSQL databases')
    identity = 'migration-test-' + uuid.uuid4().hex
    rules = rules_fixture() | dict(dataset_id=identity, publication_id=identity)
    source = psycopg.connect(source_url, autocommit=True, row_factory=dict_row)
    target = psycopg.connect(target_url, autocommit=True, row_factory=dict_row)
    for connection in (source, target):
        assert connection.execute('SELECT current_database() AS name').fetchone()['name'].endswith('_test')
    window = 'migration-window-' + uuid.uuid4().hex
    assert target.execute('SELECT count(*) AS n FROM research.budget_windows').fetchone()['n'] == 0
    now = datetime.now(timezone.utc)
    install_window(target, window, now-timedelta(minutes=1), now+timedelta(days=1),
                   dict.fromkeys(METRICS, 10**12), {}, 10**12, 100, 'fixture migration storage allowance')
    original_pause = target.execute('SELECT paused FROM research.dispatch_control WHERE singleton').fetchone()['paused']
    objects = ResultObjects(local_root=tmp_path / 'objects')
    values = [payload(rules, form) for form in ('e', 'f-two', 'c', 'd', 'b', 'a', 'f-one')]
    ordinals = [7, 9, 10, 21, 100, 999, 1001]
    try:
        for connection in (source, target):
            connection.execute("INSERT INTO research.datasets(id,name,description,adapter,capabilities) VALUES(%s,'Migration','Fixture','tft_set19','[]')", (identity,))
            connection.execute('''INSERT INTO research.publications(id,dataset_id,snapshot_id,status,manifest,scope,
                source_sha256,bundle_sha256,schema_fingerprint,published_at)
                VALUES(%s,%s,'snapshot','published','{}','{}','fixture','fixture','fixture',now())''', (identity, identity))
            connection.execute('INSERT INTO research.rulesets(publication_id,version,rules) VALUES(%s,%s,%s)', (identity, rules['version'], Jsonb(rules)))
        target.execute('UPDATE research.dispatch_control SET paused=true WHERE singleton')
        source.execute('''INSERT INTO research.query_runs(id,publication_id,ruleset_version,solver_version,query_hash,definition,
            state,progress,result_count,champion_set_count,attempts,created_at,started_at,finished_at,expires_at,error)
            VALUES(%s,%s,%s,'fixture',%s,'{"team_size":{"min":1,"max":1}}','complete','{"preserve":[null,1,1.0,true]}',
                7,6,2,'2026-11-20T00:00:00Z','2026-11-20T00:01:00Z','2026-11-20T00:02:00Z','2027-01-01T00:00:00Z',NULL)''',
                       (identity, identity, rules['version'], identity))
        for index in range(2):
            source.execute('INSERT INTO research.query_subscriptions(id,run_id,session_hash,active,created_at) VALUES(%s,%s,%s,%s,%s)',
                           (identity + '-subscription-' + str(index), identity, identity + '-session-' + str(index), bool(index), datetime(2026, 11, 20, tzinfo=timezone.utc)))
        write_results(source, identity, values, ordinals)
        yield dict(source=source, target=target, objects=objects, identity=identity, rules=rules,
                   values=values, ordinals=ordinals, scratch=tmp_path / 'scratch', target_url=target_url, budget_window=window)
    finally:
        objects.close()
        for connection in (source, target):
            connection.rollback()
            connection.execute('DELETE FROM research.query_subscriptions WHERE run_id=%s', (identity,))
            connection.execute('DELETE FROM research.query_runs WHERE id=%s', (identity,))
            connection.execute('DELETE FROM research.rulesets WHERE publication_id=%s', (identity,))
            connection.execute('DELETE FROM research.publications WHERE id=%s', (identity,))
            connection.execute('DELETE FROM research.datasets WHERE id=%s', (identity,))
        target.execute('UPDATE research.dispatch_control SET paused=%s WHERE singleton', (original_pause,))
        with target.transaction():
            target.execute('DELETE FROM research.budget_migration_receipts WHERE window_id=%s', (window,))
            target.execute('DELETE FROM research.budget_metrics WHERE window_id=%s', (window,))
            target.execute('DELETE FROM research.budget_windows WHERE id=%s', (window,))
            target.execute('DELETE FROM research.budget_storage')
        source.close()
        target.close()


def invoke(data, *, execute=True, verifier=lambda: True, target=True):
    return migration.migrate(data['source'], data['target'] if target else None, data['objects'] if execute else None,
        scratch=data['scratch'], verify_quiescence=verifier, execute=execute, run_ids=[data['identity']],
        budget_window=data['budget_window'] if execute else None)


def source_state(data):
    return {table: data['source'].execute('SELECT * FROM research.' + table +
            (' WHERE id=%s' if table == 'query_runs' else ' WHERE run_id=%s') +
            (' ORDER BY discovery_index' if table == 'query_results' else ' ORDER BY id'), (data['identity'],)).fetchall()
            for table in ('query_runs', 'query_subscriptions', 'query_results')}


def test_offline_plan_needs_no_target_credentials_or_cloud_and_changes_no_source(databases):
    before = source_state(databases)
    report = invoke(databases, execute=False, verifier=None, target=False)
    assert report['mode'] == 'plan' and report['source_modified'] is False
    assert report['queries'][0]['stored_rows'] == 7
    assert report['queries'][0]['object_count'] == 4
    assert source_state(databases) == before
    assert databases['target'].execute('SELECT 1 FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone() is None
    assert not (databases['objects'].local_root).exists()


def test_migration_preserves_metadata_subscriptions_values_orders_and_original_ordinals(databases):
    before = source_state(databases)
    observed = []
    original_put = databases['objects'].put
    def upload(key, source):
        with psycopg.connect(databases['target_url'], row_factory=dict_row) as observer:
            intent = observer.execute('SELECT state FROM research.result_object_intents WHERE key=%s', (key,)).fetchone()
            assert intent['state'] == 'planned'
            receipt = observer.execute('SELECT * FROM research.budget_migration_receipts WHERE generation_id=%s', (key.split('/')[0],)).fetchone()
            assert receipt['liability_bytes'] == MIGRATION_COPIES * receipt['retained_object_bytes']
            assert observer.execute('SELECT used FROM research.budget_storage').fetchone()['used'] == 100 + receipt['liability_bytes']
            assert observer.execute('SELECT result_generation_id FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone()['result_generation_id'] is None
        observed.append(key)
        return original_put(key, source)
    with patch.object(databases['objects'], 'put', upload):
        report = invoke(databases)
    assert len(observed) == 4
    assert source_state(databases) == before
    current = databases['target'].execute('SELECT * FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone()
    assert current['result_generation_id'] == report['queries'][0]['generation_id']
    assert {key: value for key, value in current.items() if key != 'result_generation_id'} == {key: value for key, value in before['query_runs'][0].items() if key != 'result_generation_id'}
    assert databases['target'].execute('SELECT * FROM research.query_subscriptions WHERE run_id=%s ORDER BY id', (databases['identity'],)).fetchall() == before['query_subscriptions']
    state = snapshot(databases['target'], databases['identity'])
    reader = ResultReader(databases['objects'])
    key = lambda value: value['configuration_key']
    orders = dict(active_traits=lambda value: (-value['active_trait_count'], key(value)),
                  total_cost=lambda value: (value['total_cost'] is None, value['total_cost'] or 0, key(value)),
                  champions=lambda value: (value['names_sort'].encode(), key(value)))
    for order, compare in orders.items():
        assert reader.page(databases['target'], state, order, 0, 200) == sorted(databases['values'], key=compare)
    actual = result_recovery.logical_check(reader.iter_rows(databases['target'], state), databases['scratch'])
    assert actual['rows_sha256'] == report['queries'][0]['rows_sha256']
    assert actual['stored_champion_sets'] == 6
    assert databases['target'].execute('SELECT count(*) AS n FROM research.query_results WHERE run_id=%s', (databases['identity'],)).fetchone()['n'] == 0


def test_completed_retry_reuses_verified_objects_without_puts(databases):
    first = invoke(databases)
    used = databases['target'].execute('SELECT used FROM research.budget_storage').fetchone()['used']
    with patch.object(databases['objects'], 'put', side_effect=AssertionError('Must not PUT again')):
        second = invoke(databases)
    assert second == first
    assert databases['target'].execute('SELECT used FROM research.budget_storage').fetchone()['used'] == used


def test_migration_exhausted_storage_rejects_before_first_put(databases):
    databases['target'].execute('UPDATE research.budget_storage SET ceiling=used')
    with patch.object(databases['objects'], 'put', side_effect=AssertionError('Storage must reserve before PUT')):
        with pytest.raises(BudgetUnavailable):
            invoke(databases)
    current = databases['target'].execute('SELECT result_generation_id FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone()
    assert current['result_generation_id'] is None
    assert databases['target'].execute('SELECT 1 FROM research.result_object_intents').fetchone() is None
    assert databases['target'].execute('SELECT 1 FROM research.budget_migration_receipts').fetchone() is None


def test_lost_put_acknowledgement_reuses_matching_object_on_retry(databases):
    original = databases['objects'].put
    def lost(key, source):
        original(key, source)
        raise TimeoutError('acknowledgement lost')
    with patch.object(databases['objects'], 'put', lost), pytest.raises(TimeoutError):
        invoke(databases)
    row = databases['target'].execute('SELECT result_generation_id FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone()
    assert row['result_generation_id'] is None
    with patch.object(databases['objects'], 'put', wraps=original) as repeated:
        invoke(databases)
    assert repeated.call_count == 3


def test_absent_object_after_uncertain_put_never_retries_or_releases_intent(databases):
    with patch.object(databases['objects'], 'put', side_effect=TimeoutError), pytest.raises(TimeoutError):
        invoke(databases)
    with patch.object(databases['objects'], 'put', side_effect=AssertionError('No second PUT')), pytest.raises(migration.UncertainUpload):
        invoke(databases)
    states = databases['target'].execute('SELECT state FROM research.result_object_intents WHERE generation_id IN (SELECT id FROM research.result_generations WHERE run_id=%s)', (databases['identity'],)).fetchall()
    assert states == [{'state': 'planned'}]


def test_retry_rejects_changed_source_fingerprint_before_reusing_objects(databases):
    with patch.object(databases['objects'], 'put', side_effect=TimeoutError), pytest.raises(TimeoutError):
        invoke(databases)
    first = databases['values'][0]
    databases['source'].execute('DELETE FROM research.query_results WHERE run_id=%s AND configuration_key=%s', (databases['identity'], first['configuration_key']))
    write_results(databases['source'], databases['identity'], [payload(databases['rules'], 'a', 'e')], [7])
    with patch.object(databases['objects'], 'exists', side_effect=AssertionError('Must not read changed migration objects')):
        with pytest.raises(migration.MigrationBlocked, match='fingerprint'):
            invoke(databases)


@pytest.mark.parametrize('change', ['guard', 'paused', 'metadata', 'rules', 'artifacts', 'running'])
def test_preconditions_fail_without_any_target_result_changes(databases, change):
    verifier = lambda: True
    if change == 'guard':
        verifier = lambda: 1
    elif change == 'paused':
        databases['target'].execute('UPDATE research.dispatch_control SET paused=false WHERE singleton')
    elif change == 'metadata':
        invoke(databases)
        databases['target'].execute("UPDATE research.query_runs SET error='changed' WHERE id=%s", (databases['identity'],))
    elif change == 'rules':
        altered = deepcopy(databases['rules'])
        altered['units'][0]['cost'] += 1
        databases['target'].execute('UPDATE research.rulesets SET rules=%s WHERE publication_id=%s', (Jsonb(altered), databases['identity']))
    elif change == 'artifacts':
        databases['source'].execute("INSERT INTO research.query_artifacts(run_id,format,storage_key,size,sha256,media_type,generation_hash) VALUES(%s,'json','private/old',3,'fixture','application/json','fixture')", (databases['identity'],))
    elif change == 'running':
        databases['source'].execute("UPDATE research.query_runs SET state='running' WHERE id=%s", (databases['identity'],))
    with patch.object(databases['objects'], 'put', side_effect=AssertionError('Preflight must not upload')):
        with pytest.raises(migration.MigrationBlocked):
            invoke(databases, verifier=verifier)


def test_quiescence_is_rechecked_after_put_before_publication(databases):
    allowed = [True]
    original = databases['objects'].put
    def stop(key, source):
        value = original(key, source)
        allowed[0] = False
        return value
    with patch.object(databases['objects'], 'put', stop), pytest.raises(migration.MigrationBlocked, match='quiescence'):
        invoke(databases, verifier=lambda: allowed[0])
    current = databases['target'].execute('SELECT result_generation_id FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone()
    assert current['result_generation_id'] is None


def test_source_snapshot_rejects_writes_and_plan_preserves_all_source_rows(databases):
    before = source_state(databases)
    original = migration._build_rows
    def readonly(source, *args):
        assert source.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with source.transaction():
                source.execute("UPDATE research.query_runs SET error='must never commit' WHERE id=%s", (databases['identity'],))
        yield from original(source, *args)
    with patch.object(migration, '_build_rows', readonly):
        invoke(databases, execute=False)
    assert source_state(databases) == before


def test_corrupt_uploaded_read_cannot_publish_a_pointer(databases):
    with patch.object(databases['objects'], 'read_range', return_value=b'x' * 175), pytest.raises(ValueError, match='checksum'):
        invoke(databases)
    current = databases['target'].execute('SELECT result_generation_id FROM research.query_runs WHERE id=%s', (databases['identity'],)).fetchone()
    assert current['result_generation_id'] is None


def test_terminal_partial_cache_preserves_segment_tail_and_discovery_for_every_sort(databases):
    rules = deepcopy(databases['rules'])
    base = rules['units'][0]
    rules['units'] = [{**base, 'form_id': str(index), 'unit_id': str(index), 'champion_id': str(index),
                       'name': f'Unit {index:02d}'} for index in range(20)]
    values = [payload(rules, *team) for team in islice(combinations([unit['form_id'] for unit in rules['units']], 4), 4111)]
    for connection in (databases['source'], databases['target']):
        connection.execute('UPDATE research.rulesets SET rules=%s WHERE publication_id=%s', (Jsonb(rules), databases['identity']))
    databases['source'].execute('DELETE FROM research.query_results WHERE run_id=%s', (databases['identity'],))
    databases['source'].execute("UPDATE research.query_runs SET state='resource_limited',result_count=4111,champion_set_count=4111 WHERE id=%s", (databases['identity'],))
    write_results(databases['source'], databases['identity'], values, range(101, 4212))
    report = invoke(databases)
    assert report['queries'][0]['object_count'] == 2
    state = snapshot(databases['target'], databases['identity'])
    assert state['state'] == 'resource_limited' and state['complete_orders'] is None
    assert len(state['result_tail']) == 15 * 25
    for order in ('active_traits', 'total_cost', 'champions'):
        actual = ResultReader(databases['objects']).page(databases['target'], state, order, 4050, 200)
        assert actual == values[4050:4250]
