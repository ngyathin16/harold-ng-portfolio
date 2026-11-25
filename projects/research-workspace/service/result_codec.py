"""Lossless, versioned result records against an immutable rules dictionary."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
from types import MappingProxyType
from typing import Any, Mapping


CODEC = 'dictionary-result-v1'
MAX_DICTIONARY_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024
MAX_DISCOVERY_INDEX = (1 << 63) - 1
MAX_RESULTS = 1_000_000
RECORD_BYTES = 25
_RECORD = struct.Struct('<QB8H')
_MEMBER_FIELDS = {'form_id', 'unit_id', 'champion_id', 'name', 'cost', 'slots', 'locked'}
_VARIANT_FIELDS = {'trait_id', 'name', 'member_count', 'state', 'breakpoint_id',
                   'minimum', 'evidence', 'inferred'}
_DOCUMENT_FIELDS = {'codec', 'rules_sha256', 'members', 'unit_traits', 'traits'}


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError('JSON nesting exceeds the codec limit')
    kind = type(value)
    if value is None or kind in (str, bool, int):
        return
    if kind is float and math.isfinite(value):
        return
    if kind is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if kind is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError('Result codec accepts only finite JSON values with string object keys')


def _canonical(value: Any) -> bytes:
    _json_value(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid JSON value') from exc


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _object(value: Any, fields: set[str] | None = None) -> dict:
    if type(value) is not dict or fields is not None and set(value) != fields:
        raise ValueError('Invalid dictionary object fields')
    return value


def _string(value: Any) -> str:
    if type(value) is not str:
        raise ValueError('Dictionary identities and names must be strings')
    return value


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise ValueError('Dictionary numeric fields must be integers')
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError('Non-finite JSON number: ' + value)


@dataclass(frozen=True, slots=True, init=False)
class FrozenDictionary:
    """Only the factories construct dictionaries; all stored nested values are frozen."""

    _raw: bytes
    _members: tuple[Mapping[str, Any], ...]
    _unit_traits: tuple[tuple[str, ...], ...]
    _traits: tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]
    _codes: Mapping[str, int]

    def __new__(cls):
        raise TypeError('Use FrozenDictionary.from_rules or FrozenDictionary.from_bytes')

    @classmethod
    def from_rules(cls, rules: dict) -> FrozenDictionary:
        """Capture trait evaluation outputs independently of future solver changes."""
        _object(rules)
        rules_hash = hashlib.sha256(_canonical(rules)).hexdigest()
        if type(rules.get('units')) is not list or type(rules.get('traits')) is not list:
            raise ValueError('Rules need unit and trait arrays')
        if len(rules['units']) > 65535:
            raise ValueError('Too many dictionary members')
        members = []
        unit_traits = []
        units = []
        for unit in rules['units']:
            _object(unit)
            if not _MEMBER_FIELDS <= unit.keys() or type(unit.get('traits')) is not list:
                raise ValueError('Incomplete unit in rules')
            _string(unit['form_id'])
            if not all(type(trait) is str for trait in unit['traits']):
                raise ValueError('Unit traits must be string identities')
            units.append(unit)
        for unit in sorted(units, key=lambda item: item['form_id']):
            members.append({key: unit[key] for key in _MEMBER_FIELDS})
            unit_traits.append(sorted(set(unit['traits'])))
        traits = []
        for trait in rules['traits']:
            _object(trait)
            if not {'trait_id', 'name', 'breakpoints'} <= trait.keys():
                raise ValueError('Incomplete trait in rules')
            _string(trait['trait_id'])
            _string(trait['name'])
            if type(trait['breakpoints']) is not list or type(trait.get('random_membership', False)) is not bool:
                raise ValueError('Invalid trait breakpoints or random membership')
            seen = set()
            for point in trait['breakpoints']:
                _object(point)
                if not {'breakpoint_id', 'status', 'minimum', 'evidence'} <= point.keys():
                    raise ValueError('Incomplete breakpoint')
                identity = _string(point['breakpoint_id'])
                if identity in seen:
                    raise ValueError('Duplicate breakpoint identity')
                seen.add(identity)
                _string(point['status'])
                if point['minimum'] is not None:
                    _integer(point['minimum'])
                if point['status'] == 'supported' and point['minimum'] is None:
                    raise ValueError('Supported breakpoint needs an integer minimum')
                if type(point.get('inferred', False)) is not bool:
                    raise ValueError('Breakpoint inferred flag must be boolean')
            variants = []
            for count in range(9):
                supported = [point for point in trait['breakpoints']
                             if point['status'] == 'supported' and point['minimum'] > 0
                             and count >= point['minimum']]
                uncertain = trait.get('random_membership', False) or any(
                    point['status'] == 'unknown' and count >= max(1, point['minimum'] or 1)
                    for point in trait['breakpoints'])
                point = max(supported, key=lambda item: (item['minimum'], item['breakpoint_id'])) if supported else None
                variants.append({
                    'trait_id': trait['trait_id'], 'name': trait['name'], 'member_count': count,
                    'state': 'active' if supported else 'unknown' if uncertain else 'inactive',
                    'breakpoint_id': point['breakpoint_id'] if point else None,
                    'minimum': point['minimum'] if point else None,
                    'evidence': point['evidence'] if point else trait.get('evidence'),
                    'inferred': bool(point and point.get('inferred')),
                })
            traits.append({'id': trait['trait_id'], 'variants': variants})
        return cls.from_bytes(_canonical({'codec': CODEC, 'rules_sha256': rules_hash,
                                          'members': members, 'unit_traits': unit_traits, 'traits': traits}))

    @classmethod
    def from_bytes(cls, data: bytes) -> FrozenDictionary:
        if type(data) is not bytes or not 0 < len(data) <= MAX_DICTIONARY_BYTES:
            raise ValueError('Dictionary must be bytes no larger than 4 MiB')
        try:
            document = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError('Invalid dictionary JSON') from exc
        _object(document, _DOCUMENT_FIELDS)
        canonical = _canonical(document)
        if len(canonical) > MAX_DICTIONARY_BYTES:
            raise ValueError('Canonical dictionary exceeds 4 MiB')
        if document['codec'] != CODEC:
            raise ValueError('Unsupported result dictionary version')
        if type(document['rules_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', document['rules_sha256']):
            raise ValueError('Invalid rules digest')
        members, unit_traits, traits = (document[key] for key in ('members', 'unit_traits', 'traits'))
        if any(type(items) is not list for items in (members, unit_traits, traits)):
            raise ValueError('Invalid dictionary arrays')
        if len(members) > 65535 or len(unit_traits) != len(members):
            raise ValueError('Invalid dictionary member count')
        forms = []
        for member in members:
            _object(member, _MEMBER_FIELDS)
            for key in ('form_id', 'unit_id', 'champion_id', 'name'):
                _string(member[key])
            if member['cost'] is not None:
                _integer(member['cost'])
            _integer(member['slots'])
            if type(member['locked']) is not bool:
                raise ValueError('Member locked flag must be boolean')
            forms.append(member['form_id'])
        if forms != sorted(set(forms)):
            raise ValueError('Dictionary forms must be unique and sorted')
        identities = set()
        frozen_traits = []
        for trait in traits:
            _object(trait, {'id', 'variants'})
            identity = _string(trait['id'])
            if identity in identities:
                raise ValueError('Duplicate trait identity')
            identities.add(identity)
            variants = trait['variants']
            if type(variants) is not list or len(variants) != 9:
                raise ValueError('Traits need exactly nine evaluation variants')
            for count, variant in enumerate(variants):
                _object(variant, _VARIANT_FIELDS)
                if variant['trait_id'] != identity or type(variant['trait_id']) is not str:
                    raise ValueError('Trait variant identity mismatch')
                _string(variant['name'])
                if _integer(variant['member_count']) != count:
                    raise ValueError('Trait variant count mismatch')
                if variant['state'] not in ('active', 'unknown', 'inactive'):
                    raise ValueError('Invalid trait state')
                if type(variant['inferred']) is not bool:
                    raise ValueError('Trait inferred flag must be boolean')
                if variant['state'] == 'active':
                    _string(variant['breakpoint_id'])
                    if not 0 < _integer(variant['minimum']) <= count:
                        raise ValueError('Invalid active trait minimum')
                elif variant['breakpoint_id'] is not None or variant['minimum'] is not None or variant['inferred']:
                    raise ValueError('Inactive trait carries a supported breakpoint')
            frozen_traits.append((identity, tuple(_freeze(item) for item in variants)))
        for values in unit_traits:
            if type(values) is not list or any(type(value) is not str for value in values):
                raise ValueError('Invalid unit trait identities')
            if values != sorted(set(values)) or not set(values) <= identities:
                raise ValueError('Unit traits must be unique, sorted, known identities')
        instance = object.__new__(cls)
        object.__setattr__(instance, '_raw', canonical)
        object.__setattr__(instance, '_members', tuple(_freeze(item) for item in members))
        object.__setattr__(instance, '_unit_traits', tuple(tuple(values) for values in unit_traits))
        object.__setattr__(instance, '_traits', tuple(frozen_traits))
        object.__setattr__(instance, '_codes', MappingProxyType({form: index + 1 for index, form in enumerate(forms)}))
        return instance

    def to_bytes(self) -> bytes:
        return self._raw

    def encode(self, payload: dict, discovery_index: int) -> bytes:
        if type(discovery_index) is not int or not 1 <= discovery_index <= MAX_DISCOVERY_INDEX:
            raise ValueError('Discovery index must be a positive signed PostgreSQL bigint')
        if type(payload) is not dict or type(payload.get('members')) is not list:
            raise ValueError('Result needs a member array')
        canonical = _canonical(payload)
        if len(canonical) > MAX_RESULT_BYTES:
            raise ValueError('Result payload exceeds 16 KiB')
        members = payload['members']
        if not 1 <= len(members) <= 8:
            raise ValueError('A result must contain one through eight members')
        codes = []
        for member in members:
            if type(member) is not dict or type(member.get('form_id')) is not str:
                raise ValueError('Result member needs a form identity')
            code = self._codes.get(member['form_id'])
            if code is None:
                raise ValueError('Result references an unknown form')
            codes.append(code)
        record = _RECORD.pack(discovery_index, len(codes), *(codes + [0] * (8 - len(codes))))
        _, _, expected = self._decode_record(record)
        if canonical != expected:
            raise ValueError('Result is not losslessly representable by this frozen dictionary')
        return record

    def decode(self, record: bytes) -> tuple[int, dict]:
        discovery, payload, _ = self._decode_record(record)
        return discovery, payload

    def _decode_record(self, record: bytes) -> tuple[int, dict, bytes]:
        if type(record) is not bytes or len(record) != RECORD_BYTES:
            raise ValueError('A result record must be exactly 25 bytes')
        discovery, count, *codes = _RECORD.unpack(record)
        if not 1 <= discovery <= MAX_DISCOVERY_INDEX or not 1 <= count <= 8:
            raise ValueError('Invalid result ordinal or member count')
        selected = codes[:count]
        if any(codes[count:]) or any(not 1 <= code <= len(self._members) for code in selected):
            raise ValueError('Invalid result member code or nonzero padding')
        if len(set(selected)) != count:
            raise ValueError('Duplicate result form')
        members = [_thaw(self._members[code - 1]) for code in selected]
        groups = {member['champion_id'] for member in members}
        if len(groups) != count:
            raise ValueError('Duplicate result champion')
        counts = Counter(trait for code in selected for trait in self._unit_traits[code - 1])
        evaluations = [variants[counts[identity]] for identity, variants in self._traits]
        active = [_thaw(item) for item in evaluations if item['state'] == 'active']
        unknown = [_thaw(item) for item in evaluations if item['state'] == 'unknown']
        payload = {
            'configuration_key': hashlib.sha256(_canonical(sorted(member['form_id'] for member in members))).hexdigest(),
            'champion_set_key': hashlib.sha256(_canonical(sorted(groups))).hexdigest(),
            'members': members, 'champion_count': count,
            'board_slots': sum(member['slots'] for member in members),
            'total_cost': sum(member['cost'] for member in members) if all(member['cost'] is not None for member in members) else None,
            'active_trait_count': len(active), 'traits': active, 'unknown_traits': unknown,
            'names_sort': '\x1f'.join(member['name'] for member in members),
        }
        canonical = _canonical(payload)
        if len(canonical) > MAX_RESULT_BYTES:
            raise ValueError('Result payload exceeds 16 KiB')
        return discovery, payload, canonical
