"""Single-run local correctness/timing diagnostic; does not certify hosted targets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform

import numba
import numpy

from . import build_rules, evaluate, solve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-seconds', type=float, default=120)
    args = parser.parse_args()
    rules = build_rules(args.database)
    jhin = [u['form_id'] for u in rules['units'] if u['name'] == 'Jhin']
    if len(jhin) != 1:
        raise ValueError('Selected rules do not have one unambiguous Jhin form')
    jazz = next(t for t in rules['traits'] if t['name'] == 'Jazz')
    point = next(b for b in jazz['breakpoints'] if b['minimum'] == 2 and b['status'] == 'supported')
    base = {'team_size': {'min': 8, 'max': 8}, 'board_slots': 8, 'include': jhin,
            'cost': {'max': 4}, 'min_active_traits': 8}
    narrower = dict(base, required_traits=[{'trait_id': jazz['trait_id'], 'breakpoint_id': point['breakpoint_id']}])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'benchmark-rules.json').write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding='utf-8')
    metadata = {'runs': 1, 'python': platform.python_version(), 'numpy': numpy.__version__, 'numba': numba.__version__,
                'platform': platform.platform(), 'logical_cpus': os.cpu_count(), 'hosted_target_verified': False,
                'includes': 'uncached enumeration and independent recount of every result; Jazz additionally recounted against general query',
                'excludes': 'hosted result persistence, exports, queue wait, network, concurrent load',
                'source_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}}
    for name, query in [('general', base), ('jazz', narrower)]:
        verified = 0
        def check(result):
            nonlocal verified
            if name == 'jazz':
                if evaluate([m['form_id'] for m in result['members']], base, rules) is None:
                    raise AssertionError('Jazz result is absent from the general query scope')
            verified += 1
        result = solve(query, rules, on_result=check, max_seconds=args.max_seconds)
        result['measurement'] = metadata
        result['independently_recounted_results'] = verified
        (args.output / ('benchmark-' + name + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
