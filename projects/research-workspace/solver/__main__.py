"""Build frozen rules or run an offline exhaustive query with complete exports."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

from . import build_rules, solve


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build-rules')
    build.add_argument('--database', type=Path, required=True)
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--publication-id', default='full')
    run = commands.add_parser('run')
    run.add_argument('--rules', type=Path, required=True)
    run.add_argument('--query', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--max-seconds', type=float)
    run.add_argument('--max-results', type=int)
    args = parser.parse_args()
    if args.command == 'build-rules':
        rules = build_rules(args.database, publication_id=args.publication_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, rules)
        print(rules['version'])
        return
    rules = json.loads(args.rules.read_text(encoding='utf-8'))
    query = json.loads(args.query.read_text(encoding='utf-8'))
    args.output.mkdir(parents=True, exist_ok=True)
    database = args.output / 'results.sqlite'
    if database.exists():
        raise ValueError('Choose a fresh output directory; existing results are immutable.')
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE results (configuration_key TEXT PRIMARY KEY,active INTEGER,cost INTEGER,names TEXT,payload TEXT)')
        def record(result):
            conn.execute('INSERT INTO results VALUES (?,?,?,?,?)', (result['configuration_key'], result['active_trait_count'],
                         result['total_cost'], result['names_sort'], json.dumps(result, ensure_ascii=False)))
        summary = solve(query, rules, on_result=record, max_seconds=args.max_seconds, max_results=args.max_results)
        conn.commit()
        write_json(args.output / 'summary.json', summary)
        write_json(args.output / 'rules.json', rules)
        write_json(args.output / 'query.json', summary['query'])
        fields = ['configuration_key', 'champion_count', 'board_slots', 'total_cost', 'active_trait_count',
                  'members', 'traits', 'unknown_traits']
        with (args.output / 'teams.json').open('w', encoding='utf-8') as js, (args.output / 'teams.csv').open('w', encoding='utf-8', newline='') as cs:
            js.write('[\n')
            writer = csv.DictWriter(cs, fields)
            writer.writeheader()
            for index, (payload,) in enumerate(conn.execute('SELECT payload FROM results ORDER BY active DESC,cost IS NULL,cost,names,configuration_key')):
                if index:
                    js.write(',\n')
                js.write(payload)
                item = json.loads(payload)
                writer.writerow({k: '\\N' if item[k] is None else json.dumps(item[k], ensure_ascii=False)
                                 if isinstance(item[k], (dict, list)) else item[k] for k in fields})
            js.write('\n]\n')
    artifacts = {}
    for name in ('teams.json', 'teams.csv', 'summary.json', 'rules.json', 'query.json'):
        path = args.output / name
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        artifacts[name] = {'bytes': path.stat().st_size, 'sha256': checksum}
    write_json(args.output / 'manifest.json', {'query_hash': summary['query_hash'], 'state': summary['state'],
               'complete': summary['complete'], 'count': summary['configuration_count'], 'artifacts': artifacts})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
