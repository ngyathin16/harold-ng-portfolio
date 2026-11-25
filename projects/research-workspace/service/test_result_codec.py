"""Behavioral checks for lossless records and bounded, ordered pack construction."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
from itertools import combinations, islice
import json
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.result_builder import FRAME_RECORDS, PackBuilder, ScratchLimitExceeded
from service.result_codec import FrozenDictionary, MAX_DICTIONARY_BYTES, MAX_DISCOVERY_INDEX, MAX_RESULT_BYTES, RECORD_BYTES
from solver import evaluate


def rules_fixture():
    def unit(identity, name, cost, traits, champion=None):
        return {'form_id': identity, 'unit_id': identity, 'champion_id': champion or identity,
                'name': name, 'cost': cost, 'slots': 1, 'locked': False, 'traits': traits}
    return {
        'dataset_id': 'fixture', 'snapshot_id': 'snapshot', 'publication_id': 'full', 'version': 'v1',
        'units': [unit('a', 'a', 2, ['shared', 'shared']), unit('b', 'a', 2, ['shared']),
                  unit('c', 'a\x1e', None, []), unit('d', 'é', 0, ['shared']),
                  unit('e', '😀', 4, []), unit('f-one', 'A', 2, ['shared'], 'f'),
                  unit('f-two', 'A', 2, [], 'f')],
        'traits': [
            {'trait_id': 'shared', 'name': 'Shared', 'evidence': {'at': ['base']},
             'breakpoints': [
                 {'breakpoint_id': 'shared:1a', 'minimum': 1, 'status': 'supported', 'evidence': {'at': ['a']}},
                 {'breakpoint_id': 'shared:1z', 'minimum': 1, 'status': 'supported',
                  'evidence': {'at': ['z', None, 1, 1.0, True, {'é': '😀'}]}, 'inferred': True},
                 {'breakpoint_id': 'shared:3', 'minimum': 3, 'status': 'supported', 'evidence': None},
             ]},
            {'trait_id': 'random', 'name': 'Random', 'random_membership': True,
             'breakpoints': [], 'evidence': {'at': ['random']}},
            {'trait_id': 'uncertain', 'name': 'Uncertain', 'breakpoints': [
                {'breakpoint_id': 'unknown', 'minimum': None, 'status': 'unknown', 'evidence': None}]},
        ],
    }


def payload(rules, *identities):
    result = evaluate(identities, {'team_size': {'min': 1, 'max': 8}, 'board_slots': 16}, rules)
    assert result is not None
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def test_dictionary_roundtrip_matches_independent_solver_and_preserves_nested_types():
    rules = rules_fixture()
    dictionary = FrozenDictionary.from_rules(rules)
    restored = FrozenDictionary.from_bytes(dictionary.to_bytes())
    assert restored.to_bytes() == dictionary.to_bytes()
    for size in range(1, 5):
        for team in combinations([unit['form_id'] for unit in rules['units']], size):
            expected = evaluate(team, {'team_size': {'min': 1, 'max': 8}, 'board_slots': 16}, rules)
            if expected is None:
                continue
            record = dictionary.encode(expected, MAX_DISCOVERY_INDEX)
            assert len(record) == RECORD_BYTES == 25
            ordinal, actual = restored.decode(record)
            assert ordinal == MAX_DISCOVERY_INDEX
            assert canonical(actual) == canonical(expected)
    result = payload(rules, 'a')
    assert result['traits'][0]['breakpoint_id'] == 'shared:1z'
    assert result['unknown_traits'][0]['member_count'] == 0


@pytest.mark.parametrize('ordinal', [0, -1, True, 1.0, '1', MAX_DISCOVERY_INDEX + 1])
def test_discovery_indices_reject_out_of_range_and_non_integer_values(ordinal):
    rules = rules_fixture()
    with pytest.raises(ValueError, match='Discovery index'):
        FrozenDictionary.from_rules(rules).encode(payload(rules, 'a'), ordinal)


@pytest.mark.parametrize('mutation', [
    lambda item: item.update(extra={'keep': 'me'}),
    lambda item: item.update(champion_count=True),
    lambda item: item.update(champion_count=1.0),
    lambda item: item.update(members=tuple(item['members'])),
    lambda item: item['members'][0].update(cost=2.0),
    lambda item: item['members'][0].update(locked=0),
    lambda item: item['traits'][0]['evidence']['at'].append(float('nan')),
    lambda item: item['traits'][0]['evidence']['at'].append(float('inf')),
    lambda item: item['traits'][0]['evidence']['at'].append((1, 2)),
    lambda item: item['traits'][0]['evidence'].update({1: 'not a string key'}),
])
def test_unsupported_payloads_fail_closed_without_type_coercion(mutation):
    rules = rules_fixture()
    dictionary = FrozenDictionary.from_rules(rules)
    result = payload(rules, 'a')
    mutation(result)
    with pytest.raises(ValueError):
        dictionary.encode(result, 1)


def test_dictionary_and_each_decoded_payload_are_independent_and_immutable():
    rules = rules_fixture()
    dictionary = FrozenDictionary.from_rules(rules)
    expected = deepcopy(payload(rules, 'a'))
    record = dictionary.encode(expected, 1)
    before = dictionary.to_bytes()
    rules['units'][0]['name'] = 'changed input'
    rules['traits'][0]['breakpoints'][1]['evidence']['at'][0] = 'changed input evidence'
    _, returned = dictionary.decode(record)
    returned['members'][0]['name'] = 'changed output'
    returned['traits'][0]['evidence']['at'][0] = 'changed output evidence'
    assert canonical(dictionary.decode(record)[1]) == canonical(expected)
    assert dictionary.to_bytes() == before
    with pytest.raises(TypeError):
        dictionary._members[0]['name'] = 'changed private mapping'
    with pytest.raises(FrozenInstanceError):
        dictionary._raw = b'changed attribute'
    with pytest.raises(TypeError, match='from_rules'):
        FrozenDictionary()


@pytest.mark.parametrize('record', [
    b'', b'x' * 24, b'x' * 26, bytearray(25),
    struct.pack('<QB8H', 0, 1, 1, 0, 0, 0, 0, 0, 0, 0),
    struct.pack('<QB8H', MAX_DISCOVERY_INDEX + 1, 1, 1, 0, 0, 0, 0, 0, 0, 0),
    struct.pack('<QB8H', 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    struct.pack('<QB8H', 1, 9, 1, 2, 3, 4, 5, 6, 7, 0),
    struct.pack('<QB8H', 1, 1, 0, 0, 0, 0, 0, 0, 0, 0),
    struct.pack('<QB8H', 1, 1, 65535, 0, 0, 0, 0, 0, 0, 0),
    struct.pack('<QB8H', 1, 1, 1, 0, 0, 0, 0, 0, 0, 2),
    struct.pack('<QB8H', 1, 2, 1, 1, 0, 0, 0, 0, 0, 0),
    struct.pack('<QB8H', 1, 2, 6, 7, 0, 0, 0, 0, 0, 0),
])
def test_corrupt_and_duplicate_form_or_champion_records_are_rejected(record):
    with pytest.raises(ValueError):
        FrozenDictionary.from_rules(rules_fixture()).decode(record)


@pytest.mark.parametrize('mutation', [
    lambda item: item.update(codec='future'),
    lambda item: item.update(extra=True),
    lambda item: item.update(rules_sha256='invalid'),
    lambda item: item['members'][0].update(cost=True),
    lambda item: item['members'][0].update(locked=1),
    lambda item: item['members'][0].update(slots=1.0),
    lambda item: item['members'][1].update(form_id='a'),
    lambda item: item['unit_traits'][0].append('missing'),
    lambda item: item['unit_traits'][0].append('shared'),
    lambda item: item['traits'].append(item['traits'][0]),
    lambda item: item['traits'][0]['variants'][1].update(member_count=True),
    lambda item: item['traits'][0]['variants'][1].update(minimum=2),
    lambda item: item['traits'][0]['variants'][0].update(breakpoint_id='not inactive'),
    lambda item: item['traits'][0]['variants'][1].update(extra='must preserve'),
])
def test_malformed_dictionary_is_rejected(mutation):
    document = json.loads(FrozenDictionary.from_rules(rules_fixture()).to_bytes())
    mutation(document)
    with pytest.raises(ValueError):
        FrozenDictionary.from_bytes(canonical(document))


@pytest.mark.parametrize('data', [
    b'{"codec":"dictionary-result-v1","codec":"dictionary-result-v1"}',
    b'{"number":NaN}', b'{"number":Infinity}', b'\xff', b'[]', b'',
    b' ' * (MAX_DICTIONARY_BYTES + 1),
], ids=['duplicate-key', 'nan', 'infinity', 'invalid-utf8', 'array', 'empty', 'oversize'])
def test_invalid_json_and_dictionary_byte_limit_are_rejected(data):
    with pytest.raises(ValueError):
        FrozenDictionary.from_bytes(data)


def test_rule_validation_rejects_duplicate_forms_and_unknown_traits():
    rules = rules_fixture()
    rules['units'].append(deepcopy(rules['units'][0]))
    with pytest.raises(ValueError, match='unique and sorted'):
        FrozenDictionary.from_rules(rules)
    rules = rules_fixture()
    rules['units'][0]['traits'].append('unrepresented')
    with pytest.raises(ValueError, match='known identities'):
        FrozenDictionary.from_rules(rules)


@pytest.mark.parametrize('field', ['name', 'evidence'])
def test_expanded_payload_size_is_bounded_during_encoding_and_decoding(field):
    rules = rules_fixture()
    if field == 'name':
        rules['units'][0]['name'] = 'x' * MAX_RESULT_BYTES
    else:
        rules['traits'][0]['breakpoints'][1]['evidence'] = {'large': 'x' * MAX_RESULT_BYTES}
    dictionary = FrozenDictionary.from_rules(rules)
    result = payload(rules, 'a')
    with pytest.raises(ValueError, match='16 KiB'):
        dictionary.encode(result, 1)
    record = struct.pack('<QB8H', 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match='16 KiB'):
        dictionary.decode(record)


def test_builder_preserves_all_sorts_counts_dedup_and_discovery_gaps(tmp_path):
    rules = rules_fixture()
    dictionary = FrozenDictionary.from_rules(rules)
    inputs = [payload(rules, form) for form in ('e', 'f-two', 'c', 'd', 'b', 'a', 'f-one')]
    with PackBuilder(dictionary, tmp_path, max_results=7) as builder:
        first = builder.append(inputs[:3] + inputs[:1], 100)
        second = builder.append(inputs[3:], 104)
        assert len(first) == 3
        assert len(second) == 4
        assert builder.count == 7
        assert builder.champion_set_count == 6
        assert [dictionary.decode(record)[0] for record in builder.iter_records('discovery')] == [100, 101, 102, 104, 105, 106, 107]
        ordered = {
            'active_traits': sorted(inputs, key=lambda item: (-item['active_trait_count'], item['configuration_key'])),
            'total_cost': sorted(inputs, key=lambda item: (item['total_cost'] is None, item['total_cost'] or 0, item['configuration_key'])),
            'champions': sorted(inputs, key=lambda item: (item['names_sort'].encode('utf-8'), item['configuration_key'])),
            'discovery': inputs,
        }
        for order, expected in ordered.items():
            pack = builder.build(order)
            blob = pack.path.read_bytes()
            assert len(blob) == 7 * RECORD_BYTES
            assert pack.count == 7
            assert pack.sha256 == hashlib.sha256(blob).hexdigest()
            assert pack.frame_sha256 == (pack.sha256,)
            actual = [dictionary.decode(blob[start:start + RECORD_BYTES])[1] for start in range(0, len(blob), RECORD_BYTES)]
            assert canonical(actual) == canonical(expected)
            assert builder.build(order) is pack
        with pytest.raises(RuntimeError, match='after building'):
            builder.append([], 108)
    assert not list(tmp_path.iterdir())


def test_row_cap_and_invalid_batch_roll_back_without_losing_prior_records(tmp_path):
    rules = rules_fixture()
    dictionary = FrozenDictionary.from_rules(rules)
    a, b, c = (payload(rules, form) for form in ('a', 'b', 'c'))
    with PackBuilder(dictionary, tmp_path, max_results=2) as builder:
        builder.append([a], 1)
        with pytest.raises(ValueError, match='row limit'):
            builder.append([b, c], 2)
        assert builder.count == builder.champion_set_count == 1
        assert list(builder.iter_records('discovery')) == [dictionary.encode(a, 1)]
        invalid = {**c, 'extra': 3}
        with pytest.raises(ValueError, match='losslessly'):
            builder.append([b, invalid], 2)
        assert builder.count == 1
        assert len(builder.append([a, b], 10)) == 1
        assert builder.count == builder.champion_set_count == 2
        assert [dictionary.decode(record)[0] for record in builder.iter_records('discovery')] == [1, 11]


def test_duplicate_ordinal_for_different_result_is_rejected_atomically(tmp_path):
    rules = rules_fixture()
    with PackBuilder(FrozenDictionary.from_rules(rules), tmp_path, max_results=3) as builder:
        builder.append([payload(rules, 'a')], 8)
        with pytest.raises(ValueError, match='distinct discovery'):
            builder.append([payload(rules, 'b')], 8)
        assert builder.count == 1


def test_first_write_wins_for_different_lossless_payloads_with_the_same_configuration(tmp_path):
    rules = rules_fixture()
    dictionary = FrozenDictionary.from_rules(rules)
    first = payload(rules, 'a', 'e')
    second = dictionary.decode(struct.pack('<QB8H', 99, 2, 5, 1, 0, 0, 0, 0, 0, 0))[1]
    assert first['configuration_key'] == second['configuration_key']
    assert first['names_sort'] != second['names_sort']
    with PackBuilder(dictionary, tmp_path, max_results=1) as builder:
        records = builder.append([first, second], 10)
        assert records == [dictionary.encode(first, 10)]
        assert builder.count == builder.champion_set_count == 1
        assert list(builder.iter_records('champions')) == records


def test_discovery_overflow_rolls_back_whole_append(tmp_path):
    rules = rules_fixture()
    with PackBuilder(FrozenDictionary.from_rules(rules), tmp_path, max_results=2) as builder:
        with pytest.raises(ValueError, match='Discovery index'):
            builder.append([payload(rules, 'a'), payload(rules, 'b')], MAX_DISCOVERY_INDEX)
        assert builder.count == builder.champion_set_count == 0
        assert list(builder.iter_records('discovery')) == []


@pytest.mark.parametrize('limits', [
    {'max_results': True}, {'max_results': 0}, {'max_results': 1_000_001},
    {'max_results': 1.0}, {'max_scratch_bytes': False}, {'max_scratch_bytes': 0},
])
def test_builder_limits_reject_invalid_types_and_ranges(tmp_path, limits):
    with pytest.raises(ValueError):
        PackBuilder(FrozenDictionary.from_rules(rules_fixture()), tmp_path, **limits)
    assert not list(tmp_path.iterdir())


def test_scratch_failure_is_bounded_and_prior_records_survive(tmp_path):
    rules = rules_fixture()
    for unit in rules['units']:
        unit['name'] = unit['name'] + 'x' * 7000
    dictionary = FrozenDictionary.from_rules(rules)
    budget = 140 * 1024
    with PackBuilder(dictionary, tmp_path, max_results=10, max_scratch_bytes=budget) as builder:
        first = payload(rules, 'a')
        builder.append([first], 1)
        with pytest.raises(ScratchLimitExceeded):
            builder.append([payload(rules, form) for form in ('b', 'c', 'd', 'e', 'f-one', 'f-two')], 2)
        assert builder.count == builder.champion_set_count == 1
        assert list(builder.iter_records('discovery')) == [dictionary.encode(first, 1)]
        assert sum(path.stat().st_size for path in tmp_path.rglob('*') if path.is_file()) <= budget


def test_small_scratch_reservation_fails_before_creating_files(tmp_path):
    with pytest.raises(ScratchLimitExceeded):
        PackBuilder(FrozenDictionary.from_rules(rules_fixture()), tmp_path, max_scratch_bytes=1)
    assert not list(tmp_path.iterdir())


def test_close_removes_only_owned_files_and_is_idempotent(tmp_path):
    sentinel = tmp_path / 'keep.txt'
    sentinel.write_text('keep')
    builder = PackBuilder(FrozenDictionary.from_rules(rules_fixture()), tmp_path, max_results=1)
    builder.build('discovery')
    own_directory = next(path for path in tmp_path.iterdir() if path.is_dir())
    foreign = own_directory / 'not-created-by-builder.txt'
    foreign.write_text('keep too')
    builder.close()
    builder.close()
    assert sentinel.read_text() == 'keep'
    assert foreign.read_text() == 'keep too'
    assert list(own_directory.iterdir()) == [foreign]
    with pytest.raises(RuntimeError, match='closed'):
        builder.build('discovery')


def test_empty_pack_has_no_header_and_invalid_order_does_not_seal_builder(tmp_path):
    rules = rules_fixture()
    with PackBuilder(FrozenDictionary.from_rules(rules), tmp_path, max_results=1) as builder:
        with pytest.raises(ValueError, match='Unsupported'):
            builder.build('../bad')
        assert builder.append([], 1) == []
        pack = builder.build('discovery')
        assert pack.count == 0
        assert pack.path.read_bytes() == b''
        assert pack.sha256 == hashlib.sha256(b'').hexdigest()
        assert pack.frame_sha256 == ()


def test_frame_hashes_and_arbitrary_page_sizes_across_frame_boundary(tmp_path):
    rules = rules_fixture()
    base = rules['units'][0]
    rules['units'] = [{**base, 'form_id': f'u{index:02d}', 'unit_id': f'u{index:02d}',
                       'champion_id': f'u{index:02d}', 'name': f'Unit {index:02d}'} for index in range(20)]
    dictionary = FrozenDictionary.from_rules(rules)
    expected = [payload(rules, *team) for team in islice(combinations([unit['form_id'] for unit in rules['units']], 4), FRAME_RECORDS + 13)]
    with PackBuilder(dictionary, tmp_path, max_results=len(expected)) as builder:
        builder.append(expected, 1)
        pack = builder.build('discovery')
        blob = pack.path.read_bytes()
        assert pack.frame_sha256 == tuple(hashlib.sha256(blob[start:start + FRAME_RECORDS * RECORD_BYTES]).hexdigest()
                                         for start in range(0, len(blob), FRAME_RECORDS * RECORD_BYTES))
        assert len(pack.frame_sha256) == 2
        for page_size in (1, 7, 50, 199, 200):
            for offset in (0, FRAME_RECORDS - 1, len(expected) - 1, len(expected) + 1):
                page = blob[offset * RECORD_BYTES:(offset + page_size) * RECORD_BYTES]
                actual = [dictionary.decode(page[start:start + RECORD_BYTES])[1] for start in range(0, len(page), RECORD_BYTES)]
                assert canonical(actual) == canonical(expected[offset:offset + page_size])
