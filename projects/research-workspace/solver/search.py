"""Canonical queries, independently recountable results, and resumable enumeration."""
from collections import Counter
import hashlib
import json
import time

SOLVER_VERSION = 'native-dfs-v1'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _integer(value, field, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{field} must be an integer between {minimum} and {maximum}')
    return value


def normalize_query(query, rules):
    if not isinstance(query, dict):
        raise ValueError('Query must be an object')
    allowed = {'schema_version', 'dataset_id', 'snapshot_id', 'publication_id', 'ruleset_version',
               'team_size', 'board_slots', 'include', 'exclude', 'cost', 'total_cost',
               'required_traits', 'forbidden_traits', 'min_active_traits', 'include_locked'}
    if set(query) - allowed:
        raise ValueError('Unknown query fields: ' + ', '.join(sorted(set(query) - allowed)))
    result = {'schema_version': 1}
    if type(query.get('schema_version', 1)) is not int or query.get('schema_version', 1) != 1:
        raise ValueError('Unsupported query schema')
    for key, source in [('dataset_id', 'dataset_id'), ('snapshot_id', 'snapshot_id'),
                        ('publication_id', 'publication_id'), ('ruleset_version', 'version')]:
        result[key] = query.get(key, rules[source])
        if result[key] != rules[source]:
            raise ValueError('Query and rules disagree on ' + key)
    bounds = query.get('team_size', {'min': 8, 'max': 8})
    if not isinstance(bounds, dict) or set(bounds) != {'min', 'max'}:
        raise ValueError('team_size needs min and max')
    result['team_size'] = {k: _integer(bounds[k], 'team_size.' + k, 1, 8) for k in ('min', 'max')}
    if bounds['min'] > bounds['max']:
        raise ValueError('Minimum team size exceeds maximum')
    result['board_slots'] = _integer(query.get('board_slots', 8), 'board_slots', 1, 16)
    result['min_active_traits'] = _integer(query.get('min_active_traits', 0), 'min_active_traits', 0, len(rules['traits']))
    result['include_locked'] = query.get('include_locked', True)
    if type(result['include_locked']) is not bool:
        raise ValueError('include_locked must be boolean')
    for key in ('cost', 'total_cost'):
        raw = query.get(key, {})
        if not isinstance(raw, dict) or set(raw) - {'min', 'max'}:
            raise ValueError(key + ' accepts only min and max')
        result[key] = {side: None if raw.get(side) is None else _integer(raw[side], key + '.' + side, 0, 1000)
                       for side in ('min', 'max')}
        low, high = result[key]['min'], result[key]['max']
        if low is not None and high is not None and low > high:
            raise ValueError('Minimum exceeds maximum for ' + key)
    units = {u['form_id']: u for u in rules['units']}
    traits = {t['trait_id']: t for t in rules['traits']}
    for key in ('include', 'exclude', 'forbidden_traits'):
        values = query.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(key + ' must be a list of identifiers')
        result[key] = sorted(set(values))
    if not set(result['include']) <= units.keys():
        raise ValueError('Included champion requires a known, explicit legal form')
    if not set(result['exclude']) <= units.keys() | {u['unit_id'] for u in units.values()}:
        raise ValueError('Unknown excluded champion')
    if not set(result['forbidden_traits']) <= traits.keys():
        raise ValueError('Unknown forbidden trait')
    required = query.get('required_traits', [])
    if not isinstance(required, list):
        raise ValueError('required_traits must be a list')
    normalized = {}
    for item in required:
        if not isinstance(item, dict) or set(item) != {'trait_id', 'breakpoint_id'}:
            raise ValueError('A required trait needs trait_id and breakpoint_id')
        if not all(isinstance(value, str) for value in item.values()):
            raise ValueError('Trait and breakpoint identities must be strings')
        trait = traits.get(item['trait_id'])
        if trait is None:
            raise ValueError('Unknown required trait')
        point = next((b for b in trait['breakpoints'] if b['breakpoint_id'] == item['breakpoint_id']), None)
        if point is None or point['status'] != 'supported' or type(point['minimum']) is not int or point['minimum'] <= 0:
            raise ValueError('This breakpoint cannot be proven active under this ruleset')
        if trait['trait_id'] in result['forbidden_traits']:
            raise ValueError('A trait cannot be both required and forbidden')
        previous = normalized.get(trait['trait_id'])
        if previous is None or (point['minimum'], point['breakpoint_id']) > (previous['minimum'], previous['breakpoint_id']):
            normalized[trait['trait_id']] = point
    result['required_traits'] = [{'trait_id': key, 'breakpoint_id': value['breakpoint_id']}
                                 for key, value in sorted(normalized.items())]
    excluded_groups = {u['champion_id'] for u in units.values()
                       if u['form_id'] in result['exclude'] or u['unit_id'] in result['exclude']}
    included = [units[key] for key in result['include']]
    if len({u['champion_id'] for u in included}) != len(included):
        raise ValueError('Included forms represent the same or mutually exclusive champion')
    if len(included) > bounds['max'] or sum(u['slots'] for u in included) > result['board_slots']:
        raise ValueError('Included champions exceed team size or board capacity')
    for unit in included:
        if unit['champion_id'] in excluded_groups or not _eligible(unit, result):
            raise ValueError('Included champion conflicts with exclusion, cost, or locked filters: ' + unit['name'])
    if result['total_cost']['max'] is not None and sum(u['cost'] for u in included) > result['total_cost']['max']:
        raise ValueError('Included champions exceed total cost')
    return result


def _eligible(unit, query):
    if unit['locked'] and not query['include_locked']:
        return False
    cost = unit['cost']
    for side, value in query['cost'].items():
        if value is not None and (cost is None or (cost < value if side == 'min' else cost > value)):
            return False
    if cost is None and any(v is not None for v in query['total_cost'].values()):
        return False
    return True


def query_key(query, rules):
    return hashlib.sha256(_json({'query': normalize_query(query, rules), 'solver_version': SOLVER_VERSION}).encode()).hexdigest()


def _trait_evaluations(members, rules):
    counts = Counter(t for u in members for t in set(u['traits']))
    evaluations = []
    for trait in rules['traits']:
        count = counts[trait['trait_id']]
        supported = [b for b in trait['breakpoints'] if b['status'] == 'supported' and b['minimum'] > 0 and count >= b['minimum']]
        uncertain = trait.get('random_membership', False) or any(
            b['status'] == 'unknown' and count >= max(1, b['minimum'] or 1) for b in trait['breakpoints'])
        state = 'active' if supported else 'unknown' if uncertain else 'inactive'
        point = max(supported, key=lambda b: (b['minimum'], b['breakpoint_id'])) if supported else None
        evaluations.append({'trait_id': trait['trait_id'], 'name': trait['name'], 'member_count': count,
                            'state': state, 'breakpoint_id': point['breakpoint_id'] if point else None,
                            'minimum': point['minimum'] if point else None,
                            'evidence': point['evidence'] if point else trait.get('evidence'),
                            'inferred': bool(point and point.get('inferred'))})
    return evaluations


def evaluate(team_ids, query, rules):
    """Recount a configuration using plain Python; no search state is trusted."""
    q = normalize_query(query, rules)
    units = {u['form_id']: u for u in rules['units']}
    if len(team_ids) != len(set(team_ids)) or not set(team_ids) <= units.keys():
        return None
    members = sorted((units[i] for i in team_ids), key=lambda u: (u['name'], u['form_id']))
    groups = {u['champion_id'] for u in members}
    excluded = {u['champion_id'] for u in units.values() if u['form_id'] in q['exclude'] or u['unit_id'] in q['exclude']}
    if len(groups) != len(members) or groups & excluded or not set(q['include']) <= set(team_ids):
        return None
    if not q['team_size']['min'] <= len(members) <= q['team_size']['max']:
        return None
    slots = sum(u['slots'] for u in members)
    if slots > q['board_slots'] or not all(_eligible(u, q) for u in members):
        return None
    cost = sum(u['cost'] for u in members) if all(u['cost'] is not None for u in members) else None
    for side, value in q['total_cost'].items():
        if value is not None and (cost is None or (cost < value if side == 'min' else cost > value)):
            return None
    traits = _trait_evaluations(members, rules)
    by_trait = {t['trait_id']: t for t in traits}
    active = sum(t['state'] == 'active' for t in traits)
    if active < q['min_active_traits'] or any(by_trait[t]['state'] != 'inactive' for t in q['forbidden_traits']):
        return None
    for required in q['required_traits']:
        rule = next(t for t in rules['traits'] if t['trait_id'] == required['trait_id'])
        minimum = next(b['minimum'] for b in rule['breakpoints'] if b['breakpoint_id'] == required['breakpoint_id'])
        actual = by_trait[required['trait_id']]
        if actual['state'] != 'active' or actual['minimum'] < minimum:
            return None
    key = hashlib.sha256(_json(sorted(team_ids)).encode()).hexdigest()
    return {'configuration_key': key, 'champion_set_key': hashlib.sha256(_json(sorted(groups)).encode()).hexdigest(),
            'members': [{'form_id': u['form_id'], 'unit_id': u['unit_id'], 'champion_id': u['champion_id'],
                         'name': u['name'], 'cost': u['cost'], 'slots': u['slots'], 'locked': u['locked']} for u in members],
            'champion_count': len(members), 'board_slots': slots, 'total_cost': cost, 'active_trait_count': active,
            'traits': [t for t in traits if t['state'] == 'active'],
            'unknown_traits': [t for t in traits if t['state'] == 'unknown'],
            'names_sort': '\u001f'.join(u['name'] for u in members)}


def solve(query, rules, *, on_result=None, on_progress=None, cancelled=None, max_seconds=None, max_results=None):
    """Stream unique configurations. Only exhausted searches return complete."""
    import numpy as np
    from .native import advance
    q = normalize_query(query, rules)
    if max_seconds is not None and (not isinstance(max_seconds, (int, float)) or max_seconds <= 0):
        raise ValueError('max_seconds must be positive')
    if max_results is not None:
        _integer(max_results, 'max_results', 1)
    started = time.monotonic()
    unit_map = {u['form_id']: u for u in rules['units']}
    seed = [unit_map[i] for i in q['include']]
    excluded = {u['champion_id'] for u in rules['units'] if u['form_id'] in q['exclude'] or u['unit_id'] in q['exclude']}
    excluded.update(u['champion_id'] for u in seed)
    units = [u for u in rules['units'] if u['champion_id'] not in excluded and _eligible(u, q)]
    units.sort(key=lambda u: u['form_id'])
    index = {t['trait_id']: i for i, t in enumerate(rules['traits'])}
    ntraits, n = len(index), len(units)
    thresholds = np.full(ntraits, 1000000, dtype=np.int64)
    required = np.zeros(ntraits, dtype=np.int64)
    forbidden = np.full(ntraits, 1000000, dtype=np.int64)
    for trait in rules['traits']:
        i = index[trait['trait_id']]
        supported = [b['minimum'] for b in trait['breakpoints'] if b['status'] == 'supported' and b['minimum'] > 0]
        if supported:
            thresholds[i] = min(supported)
        if trait['trait_id'] in q['forbidden_traits']:
            possible = [max(1, b['minimum'] or 1) for b in trait['breakpoints'] if b['status'] in ('supported', 'unknown')]
            forbidden[i] = 0 if trait.get('random_membership') else min(possible, default=1000000)
        for item in q['required_traits']:
            if item['trait_id'] == trait['trait_id']:
                required[i] = next(b['minimum'] for b in trait['breakpoints'] if b['breakpoint_id'] == item['breakpoint_id'])
    memberships = np.zeros((n, ntraits), dtype=np.int64)
    for i, unit in enumerate(units):
        for trait in set(unit['traits']):
            memberships[i, index[trait]] = 1
    counts = np.zeros(ntraits, dtype=np.int64)
    for unit in seed:
        for trait in set(unit['traits']):
            counts[index[trait]] += 1
    suffix = np.zeros((n + 1, ntraits), dtype=np.int64)
    max_memberships = np.zeros(n + 1, dtype=np.int64)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + memberships[i]
        max_memberships[i] = max(max_memberships[i + 1], int(np.sum(memberships[i])))
    group_names = sorted({u['champion_id'] for u in units})
    group_map = {name: i for i, name in enumerate(group_names)}
    groups = np.array([group_map[u['champion_id']] for u in units], dtype=np.int64)
    slots = np.array([u['slots'] for u in units], dtype=np.int64)
    costs = np.array([u['cost'] or 0 for u in units], dtype=np.int64)
    picked = np.zeros(8, dtype=np.int64)
    next_index = np.zeros(9, dtype=np.int64)
    entered = np.zeros(9, dtype=np.int64)
    slot_sums = np.zeros(9, dtype=np.int64)
    cost_sums = np.zeros(9, dtype=np.int64)
    slot_sums[0], cost_sums[0] = sum(u['slots'] for u in seed), sum(u['cost'] or 0 for u in seed)
    depth = np.zeros(1, dtype=np.int64)
    output = np.zeros((512, 9), dtype=np.int64)
    found = visited = 0
    champion_sets = set()
    state, reason = 'running', None
    while state == 'running':
        if cancelled and cancelled():
            state, reason = 'cancelled', 'Cancellation requested'
            break
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            state, reason = 'resource_limited', 'Execution time limit reached'
            break
        emitted, explored, done = advance(memberships, groups, slots, costs, thresholds, required, forbidden,
            suffix, max_memberships, counts, picked, next_index, entered, slot_sums, cost_sums, depth,
            len(seed), q['team_size']['min'], q['team_size']['max'], q['board_slots'], q['min_active_traits'],
            q['total_cost']['min'] or 0, q['total_cost']['max'] if q['total_cost']['max'] is not None else 1000000000,
            20000, output)
        visited += explored
        for row in output[:emitted]:
            selected = q['include'] + [units[int(i)]['form_id'] for i in row[1:1 + int(row[0])]]
            result = evaluate(selected, q, rules)
            if result is None:
                raise RuntimeError('Independent result recount rejected a solver result')
            found += 1
            champion_sets.add(result['champion_set_key'])
            if on_result:
                on_result(result)
            if max_results is not None and found >= max_results:
                state, reason = 'resource_limited', 'Result limit reached; search is incomplete'
                break
        progress = {'state': state, 'visited_nodes': visited, 'configuration_count': found,
                    'elapsed_seconds': time.monotonic() - started}
        if on_progress:
            on_progress(progress)
        if done and state == 'running':
            state = 'complete'
    return {'state': state, 'complete': state == 'complete', 'reason': reason,
            'configuration_count': found, 'champion_set_count': len(champion_sets), 'visited_nodes': visited,
            'elapsed_seconds': time.monotonic() - started, 'query': q, 'query_hash': query_key(q, rules),
            'ruleset_version': rules['version'], 'solver_version': SOLVER_VERSION,
            'completeness_scope': 'All matches under these recorded rules and assumptions' if state == 'complete' else 'Incomplete'}
