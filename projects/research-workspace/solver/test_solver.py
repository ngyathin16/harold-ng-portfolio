import copy
from itertools import combinations
from pathlib import Path
import random
import json
import hashlib
import subprocess
import sys
import tempfile
import unittest

from solver import build_rules, evaluate, normalize_query, query_key, solve


def fixture():
    def trait(identity, thresholds, status='supported', unique=False):
        return {'trait_id': identity, 'name': identity, 'unique': unique,
                'breakpoints': [{'breakpoint_id': identity + ':' + str(i), 'minimum': n,
                                 'status': status, 'evidence': {'source_path': identity}}
                                for i, n in enumerate(thresholds)], 'evidence': {}}
    def unit(identity, traits, cost=2, slots=1, group=None, locked=False):
        return {'form_id': identity, 'unit_id': identity.split(':')[0], 'champion_id': group or identity,
                'name': identity, 'traits': traits, 'cost': cost, 'slots': slots, 'locked': locked}
    return {'dataset_id': 'fixture', 'snapshot_id': 's1', 'publication_id': 'full', 'version': 'r1',
            'traits': [trait('Jazz', [2, 3]), trait('Other', [2, 4]), trait('Unique', [1], unique=True),
                       trait('Conditional', [1], 'unknown'), trait('Placeholder', [0], 'unknown')],
            'units': [unit('a', ['Jazz', 'Unique']), unit('b', ['Jazz', 'Other']), unit('c', ['Other']),
                      unit('d', ['Jazz', 'Conditional'], cost=4), unit('e', ['Other'], cost=5),
                      unit('f:one', ['Jazz'], group='f', locked=True),
                      unit('f:two', ['Other'], group='f', locked=True),
                      unit('big', ['Jazz', 'Other'], slots=2), unit('null', ['Placeholder'], cost=None)]}


def brute_force(query, rules):
    """Test oracle uses combinations and independently counts each candidate."""
    q = normalize_query(query, rules)
    result = set()
    excluded = {u['champion_id'] for u in rules['units']
                if u['form_id'] in q['exclude'] or u['unit_id'] in q['exclude']}
    for size in range(q['team_size']['min'], q['team_size']['max'] + 1):
        for team in combinations(rules['units'], size):
            identities = {u['form_id'] for u in team}
            if not set(q['include']) <= identities or len({u['champion_id'] for u in team}) != size:
                continue
            if any(u['champion_id'] in excluded or u['locked'] and not q['include_locked'] for u in team):
                continue
            if sum(u['slots'] for u in team) > q['board_slots']:
                continue
            costs = [u['cost'] for u in team]
            if any(v is not None and any(c is None or (c < v if side == 'min' else c > v) for c in costs)
                   for side, v in q['cost'].items()):
                continue
            total = None if None in costs else sum(costs)
            if any(v is not None and (total is None or (total < v if side == 'min' else total > v))
                   for side, v in q['total_cost'].items()):
                continue
            active = {}
            unknown = set()
            for trait in rules['traits']:
                count = sum(trait['trait_id'] in u['traits'] for u in team)
                levels = [b['minimum'] for b in trait['breakpoints'] if b['status'] == 'supported' and
                          b['minimum'] > 0 and count >= b['minimum']]
                if levels:
                    active[trait['trait_id']] = max(levels)
                elif trait.get('random_membership') or any(b['status'] == 'unknown' and count >= max(1, b['minimum'] or 1)
                                                          for b in trait['breakpoints']):
                    unknown.add(trait['trait_id'])
            if len(active) < q['min_active_traits'] or (set(active) | unknown) & set(q['forbidden_traits']):
                continue
            passed = True
            for required in q['required_traits']:
                point = next(b for t in rules['traits'] for b in t['breakpoints'] if b['breakpoint_id'] == required['breakpoint_id'])
                if active.get(required['trait_id'], -1) < point['minimum']:
                    passed = False
            if passed:
                result.add(tuple(sorted(identities)))
    return result


class SolverTests(unittest.TestCase):
    def run_query(self, query, rules=None, **options):
        results = []
        summary = solve(query, rules or fixture(), on_result=results.append, **options)
        keys = {tuple(sorted(m['form_id'] for m in r['members'])) for r in results}
        self.assertEqual(len(keys), len(results))
        return keys, summary, results

    def test_independent_brute_force_across_varied_filters(self):
        rng = random.Random(19)
        for _ in range(35):
            query = {'team_size': {'min': rng.randint(1, 3), 'max': 4}, 'board_slots': rng.randint(3, 5),
                     'min_active_traits': rng.randint(0, 3), 'include_locked': rng.choice([True, False]),
                     'cost': {'max': rng.choice([None, 4, 5])}, 'total_cost': {'max': rng.choice([None, 8, 12])},
                     'forbidden_traits': rng.choice([[], ['Conditional'], ['Jazz']])}
            if not query['forbidden_traits'] and rng.choice([True, False]):
                query['required_traits'] = [{'trait_id': 'Jazz', 'breakpoint_id': 'Jazz:0'}]
            actual, summary, _ = self.run_query(query)
            self.assertEqual(actual, brute_force(query, fixture()), query)
            self.assertEqual(summary['state'], 'complete')

    def test_generic_trait_filter_is_subset_and_higher_tiers_qualify(self):
        q = {'team_size': {'min': 3, 'max': 3}, 'cost': {'max': 4}, 'include': ['a']}
        all_results, _, _ = self.run_query(q)
        for trait in ('Jazz', 'Other'):
            narrower = dict(q, required_traits=[{'trait_id': trait, 'breakpoint_id': trait + ':0'}])
            selected, _, rows = self.run_query(narrower)
            self.assertTrue(selected <= all_results)
            self.assertEqual(selected, brute_force(narrower, fixture()))
        self.assertIsNotNone(evaluate(['a', 'b', 'd'], dict(q, required_traits=[{'trait_id': 'Jazz', 'breakpoint_id': 'Jazz:0'}]), fixture()))

    def test_distinct_champions_slots_unknown_cost_and_unique_traits(self):
        q = {'team_size': {'min': 1, 'max': 3}, 'board_slots': 2, 'cost': {'max': 4}}
        results, _, rows = self.run_query(q)
        self.assertFalse(any('f:one' in r and 'f:two' in r for r in results))
        self.assertFalse(any('null' in r or 'e' in r for r in results))
        self.assertFalse(any(len(r) > 1 and 'big' in r for r in results))
        single = next(r for r in rows if [m['form_id'] for m in r['members']] == ['a'])
        self.assertEqual(single['active_trait_count'], 1)
        self.assertIsNone(evaluate(['a', 'a'], q, fixture()))

    def test_unknown_is_not_inactive_and_zero_placeholder_never_activates(self):
        q = {'team_size': {'min': 1, 'max': 1}}
        row = evaluate(['null'], q, fixture())
        self.assertEqual(row['active_trait_count'], 0)
        self.assertEqual(row['unknown_traits'][0]['name'], 'Placeholder')
        self.assertIsNone(evaluate(['d'], dict(q, forbidden_traits=['Conditional']), fixture()))
        self.assertIsNotNone(evaluate(['c'], dict(q, forbidden_traits=['Conditional']), fixture()))

    def test_contradictions_fail_before_work(self):
        for query in ({'include': ['f:one', 'f:two']}, {'include': ['a'], 'exclude': ['a']},
                      {'include': ['e'], 'cost': {'max': 4}}, {'include': ['f:one'], 'include_locked': False},
                      {'required_traits': [{'trait_id': 'Conditional', 'breakpoint_id': 'Conditional:0'}]},
                      {'team_size': {'min': 8, 'max': 1}}, {'board_slots': True}, {'typo': 1}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                normalize_query(query, fixture())

    def test_excluding_one_form_excludes_whole_champion(self):
        q = {'team_size': {'min': 1, 'max': 2}, 'exclude': ['f:one']}
        results, _, _ = self.run_query(q)
        self.assertFalse(any('f:one' in r or 'f:two' in r for r in results))

    def test_query_hash_scope_and_filter_order(self):
        self.assertEqual(query_key({'include': ['a', 'b']}, fixture()), query_key({'include': ['b', 'a']}, fixture()))
        self.assertNotEqual(query_key({}, fixture()), query_key({'required_traits': [{'trait_id': 'Jazz', 'breakpoint_id': 'Jazz:0'}]}, fixture()))
        with self.assertRaises(ValueError):
            normalize_query({'snapshot_id': 's2'}, fixture())

    def test_partial_and_cancelled_runs_never_complete(self):
        q = {'team_size': {'min': 1, 'max': 3}}
        _, summary, _ = self.run_query(q, max_results=1)
        self.assertEqual(summary['state'], 'resource_limited')
        self.assertFalse(summary['complete'])
        _, summary, _ = self.run_query(q, cancelled=lambda: True)
        self.assertEqual(summary['state'], 'cancelled')
        self.assertFalse(summary['complete'])

    def test_seeded_search_matches_oracle_and_complete_empty_is_valid(self):
        q = {'team_size': {'min': 2, 'max': 4}, 'include': ['b'], 'min_active_traits': 2}
        result, _, _ = self.run_query(q)
        self.assertEqual(result, brute_force(q, fixture()))
        _, summary, _ = self.run_query({'team_size': {'min': 1, 'max': 1}, 'min_active_traits': 5})
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['configuration_count'], 0)

    def test_real_snapshot_rules_preserve_forms_and_explicit_unknowns(self):
        root = Path(__file__).resolve().parents[2] / 'tft-set-19'
        if not (root / 'data/latest.json').is_file():
            self.skipTest('Local research snapshot unavailable')
        latest = json.loads((root / 'data/latest.json').read_text())
        rules = build_rules(root / latest['database'])
        self.assertEqual(rules, build_rules(root / latest['database']))
        sona = [u for u in rules['units'] if u['unit_id'] == 'tft19:unit:DA_Sona19']
        bands = {'tft19:trait:' + k for k in ('DA_Heartsteel19', 'DA_IonicSpark19', 'DA_KDA19', 'DA_Pentakill19', 'DA_TrueDamage19')}
        self.assertEqual(len(sona), 5)
        self.assertTrue(all(len(bands & set(u['traits'])) == 1 for u in sona))
        seraphine = [u for u in rules['units'] if u['unit_id'] == 'tft19:unit:DA_Seraphine19_Indie']
        self.assertEqual(len(seraphine), 3)
        self.assertTrue(all(len(u['traits']) == 1 for u in seraphine))
        self.assertEqual(len({u['champion_id'] for u in seraphine}), 1)
        akali = [u for u in rules['units'] if u['unit_id'] in ('tft19:unit:DA_AkaliKDA19', 'tft19:unit:DA_Akali_TrueDamage19')]
        self.assertEqual(len({u['champion_id'] for u in akali}), 1)
        baron = next(u for u in rules['units'] if u['unit_id'] == 'tft19:unit:DA_BaronNashor19')
        self.assertEqual(baron['slots'], 2)
        self.assertFalse(baron['slot_assumption'])
        collab = next(t for t in rules['traits'] if t['name'] == 'Collab')
        self.assertTrue(collab['random_membership'])
        self.assertTrue(all(b['status'] == 'unknown' for b in collab['breakpoints']))
        beach = next(t for t in rules['traits'] if t['name'] == 'Beach Pop')
        self.assertTrue(beach['breakpoints'][0]['inferred'])
        self.assertGreater(beach['breakpoints'][0]['minimum'], 0)

    def test_complete_exports_include_all_rows_and_verified_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = fixture()
            query = {'team_size': {'min': 1, 'max': 3}}
            (root / 'rules.json').write_text(json.dumps(rules), encoding='utf-8')
            (root / 'query.json').write_text(json.dumps(query), encoding='utf-8')
            subprocess.run([sys.executable, '-m', 'solver', 'run', '--rules', str(root / 'rules.json'),
                            '--query', str(root / 'query.json'), '--output', str(root / 'run')],
                           cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)
            manifest = json.loads((root / 'run/manifest.json').read_text())
            exported = json.loads((root / 'run/teams.json').read_text())
            self.assertTrue(manifest['complete'])
            self.assertEqual(len(exported), len(brute_force(query, rules)))
            self.assertEqual(len(exported), manifest['count'])
            self.assertTrue(any(r['total_cost'] is None for r in exported))
            for filename, expected in manifest['artifacts'].items():
                content = (root / 'run' / filename).read_bytes()
                self.assertEqual(len(content), expected['bytes'])
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected['sha256'])

    def test_bounds_derive_from_rules_even_with_more_than_three_memberships(self):
        rng = random.Random(482)
        rules = fixture()
        rules['traits'] = [{'trait_id': f't{i}', 'name': f't{i}', 'evidence': {}, 'breakpoints': [
            {'breakpoint_id': f't{i}:1', 'minimum': 1 + i % 3, 'status': 'supported', 'evidence': {}}]}
                           for i in range(9)]
        rules['units'] = [{'form_id': f'u{i}', 'unit_id': f'u{i}', 'champion_id': f'u{i}', 'name': f'u{i}',
                           'cost': 1 + i % 4, 'slots': 1, 'locked': False,
                           'traits': sorted(rng.sample([t['trait_id'] for t in rules['traits']], 5))} for i in range(12)]
        for active in (3, 5, 7, 9):
            query = {'team_size': {'min': 2, 'max': 4}, 'min_active_traits': active, 'include': ['u0']}
            actual, summary, _ = self.run_query(query, rules)
            self.assertTrue(summary['complete'])
            self.assertEqual(actual, brute_force(query, rules))

    def test_batch_resumption_does_not_skip_or_duplicate_results(self):
        rules = fixture()
        rules['units'] = [dict(rules['units'][0], form_id=f'u{i}', unit_id=f'u{i}', champion_id=f'u{i}', name=f'u{i}')
                          for i in range(15)]
        query = {'team_size': {'min': 4, 'max': 4}}
        results, summary, _ = self.run_query(query, rules)
        self.assertEqual(len(results), 1365)
        self.assertEqual(results, brute_force(query, rules))
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 1
        _, summary, _ = self.run_query(query, rules, cancelled=cancelled)
        self.assertEqual(summary['state'], 'cancelled')
        self.assertEqual(summary['configuration_count'], 512)


if __name__ == '__main__':
    unittest.main()
