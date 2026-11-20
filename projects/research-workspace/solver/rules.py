"""Set 19 adapter. Interpretations are versioned alongside their evidence."""
import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3


def build_rules(sqlite_path, dataset_id='tft-set-19', publication_id='full'):
    path = Path(sqlite_path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        snapshot = dict(conn.execute('SELECT * FROM snapshots').fetchone())
        if snapshot['set_number'] != 19:
            raise ValueError('This rules adapter supports only Set 19')
        entities = {r['entity_id']: dict(r) for r in conn.execute('SELECT * FROM entities')}
        by_key = {r['source_key']: r for r in entities.values() if r['kind'] == 'unit'}
        traits = []
        for row in conn.execute('SELECT * FROM traits ORDER BY entity_id'):
            entity = entities[row['entity_id']]
            key = entity['source_key']
            evidence = {'source_id': entity['source_id'], 'source_path': entity['source_path'],
                        'entity_id': entity['entity_id']}
            unknown = key in ('DA_Collab19', 'DA_Alistar19_UniqueTrait', 'DA_Amumu19_UniqueTrait',
                              'DA_Teemo19_UniqueTrait')
            breaks = []
            for point in conn.execute('SELECT * FROM trait_breakpoints WHERE trait_id=? ORDER BY tier', (row['entity_id'],)):
                minimum = point['min_units']
                status = 'unknown' if unknown or minimum is None or minimum <= 0 else 'supported'
                reason = 'Additional activation conditions are not established by this final-board model.' if unknown else None
                if point['style'] == '5':
                    status, reason = 'unavailable', 'Quest tiers require external progression excluded by this ruleset.'
                if minimum is None or minimum <= 0:
                    reason = 'Zero or missing source threshold is a placeholder, not automatic activation.'
                breaks.append({'breakpoint_id': row['entity_id'] + ':tier:' + str(point['tier']),
                               'minimum': minimum, 'status': status, 'reason': reason,
                               'evidence': dict(evidence, source_path=point['source_path'])})
            if key == 'DA_OceanSong19':
                points = [dict(p) for p in conn.execute('SELECT * FROM supplemental_beach_pop_instruments ORDER BY threshold')]
                if points:
                    minimum = min(p['threshold'] for p in points)
                    source = next(p for p in points if p['threshold'] == minimum)
                    breaks = [{'breakpoint_id': row['entity_id'] + ':inferred:' + str(minimum),
                               'minimum': minimum, 'status': 'supported', 'inferred': True,
                               'reason': 'First instrument threshold used as first activation threshold.',
                               'evidence': {'source_id': source['source_id'], 'source_path': source['source_path']}}]
            traits.append({'trait_id': row['entity_id'], 'name': entity['name'],
                           'unique': row['trait_type'] == 'unique', 'breakpoints': breaks,
                           'random_membership': key == 'DA_Collab19', 'evidence': evidence})
        trait_ids = {t['trait_id'] for t in traits}
        groups, group_evidence, slot_overrides = {}, {}, {}
        for row in conn.execute('SELECT * FROM curated_records'):
            fields = json.loads(row['fields_json'])
            keys = fields.get('mutually_exclusive_unlock_targets', [])
            if keys:
                group = 'exclusive:' + ':'.join(sorted(keys))
                groups.update({key: group for key in keys})
                group_evidence.update({key: row['record_id'] for key in keys})
            if isinstance(fields.get('board_slots'), int) and fields.get('unit_source_key'):
                slot_overrides[fields['unit_source_key']] = (fields['board_slots'], row['record_id'])
        units = []
        for row in conn.execute('SELECT * FROM units WHERE shop_unit=1 ORDER BY entity_id'):
            entity = entities[row['entity_id']]
            if entity['lifecycle_status'] in ('disabled', 'legacy'):
                continue
            raw = json.loads(entity['raw_json'])
            key = entity['source_key']
            slots = row['board_slots']
            slot_evidence = None
            if key in slot_overrides:
                slots, slot_evidence = slot_overrides[key]
            assumed_slots = slots is None
            if slots is None:
                slots = 1
            forms = [(row['entity_id'], None, raw, entity['source_path'])]
            if raw.get('variantTraitApiNames'):
                extra = [(row['entity_id'] + ':form:' + k, k, v, entity['source_path'] + '.extraAbilities[' + json.dumps(k) + ']')
                         for k, v in sorted(raw.get('extraAbilities', {}).items())
                         if v.get('traitApiNames')]
                if extra:
                    forms = extra
            if key == 'DA_Seraphine19_Indie' and 'TFT19_Seraphine' in by_key:
                rising = by_key['TFT19_Seraphine']
                rising_raw = json.loads(rising['raw_json'])
                forms.append((row['entity_id'] + ':form:TFT19_Seraphine', 'TFT19_Seraphine', rising_raw, rising['source_path']))
                forms.extend((row['entity_id'] + ':form:' + k, k, v, rising['source_path'] + '.extraAbilities[' + json.dumps(k) + ']')
                             for k, v in sorted(rising_raw.get('extraAbilities', {}).items())
                             if v.get('traitApiNames'))
            for form_id, form_name, form, form_path in forms:
                memberships = sorted({'tft19:trait:' + k for k in form.get('traitApiNames', [])})
                if not set(memberships) <= trait_ids:
                    raise ValueError('Rules contain an unresolved trait membership: ' + form_id)
                if any(entities[t]['source_key'] == 'DA_MysticInstrument19' for t in memberships):
                    continue
                label = entity['name']
                if form_name:
                    band_names = [entities[t]['name'] for t in memberships if t not in
                                  {'tft19:trait:' + k for k in raw.get('traitApiNames', [])}]
                    label += ' (' + ', '.join(band_names or [form_name]) + ')'
                if key in groups:
                    label += ' (' + ', '.join(entities[t]['name'] for t in memberships) + ')'
                units.append({'form_id': form_id, 'unit_id': row['entity_id'], 'champion_id': groups.get(key, row['entity_id']),
                              'name': label, 'cost': row['cost'], 'slots': slots, 'locked': bool(row['locked']),
                              'traits': memberships, 'applicability': entity['lifecycle_status'],
                              'slot_assumption': assumed_slots, 'slot_evidence': slot_evidence,
                              'identity_evidence': group_evidence.get(key),
                              'evidence': {'source_id': entity['source_id'], 'source_path': form_path,
                                           'form_source_key': form_name}})
        rules = {'dataset_id': dataset_id, 'snapshot_id': snapshot['snapshot_id'], 'publication_id': publication_id,
                 'adapter_version': 'set19-native-v1', 'units': units, 'traits': traits,
                 'assumptions': [
                     'Hypothetical final boards; locked champions do not imply completed unlock sequences.',
                     'One star per champion. K/DA contributions therefore equal selected K/DA members.',
                     'One occupied slot where source slot usage is unknown; explicit slot exceptions override this.',
                     'Native memberships only; no emblems, augments, external grants, positioning, or combat simulation.',
                     'Unique traits count once by native membership when activation is supported; effect triggers are not simulated.',
                     'Country, Emo, Hype Man and random Collab activation remain Unknown.',
                     'Quest tiers are unavailable without external progression. Zero placeholders never activate automatically.',
                     'Beach Pop activation is inferred from its first instrument threshold.',
                     'Sona selects one source-defined band. Seraphine selects Indie, Rising Star or Superstar, replacing her unique trait.',
                 ]}
    encoded = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    rules['version'] = 'set19-' + hashlib.sha256(encoded.encode()).hexdigest()[:20]
    return rules
