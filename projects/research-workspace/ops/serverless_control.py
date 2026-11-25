"""Independently authenticated operator pause/resume and capacity measurements.

Use a direct verify-full operator connection. Values describe provider-accounted
storage including charged history; a local pg_database_size is not a substitute.
"""
import argparse
from datetime import datetime, timezone
import json
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from service.admission import record_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('pause')
    commands.add_parser('resume')
    commands.add_parser('status')
    sample = commands.add_parser('capacity')
    sample.add_argument('--used-bytes', type=int, required=True)
    sample.add_argument('--limit-bytes', type=int, required=True)
    sample.add_argument('--measured-at', required=True, help='Provider measurement ISO8601 UTC timestamp')
    args = parser.parse_args()
    url = os.environ.get('RESEARCH_OPERATOR_DATABASE_URL', '')
    if conninfo_to_dict(url).get('sslmode') != 'verify-full':
        parser.error('RESEARCH_OPERATOR_DATABASE_URL must use sslmode=verify-full and a direct/session-affine endpoint')
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        if args.command == 'capacity':
            measured = datetime.fromisoformat(args.measured_at.replace('Z', '+00:00'))
            if measured.tzinfo is None or measured > datetime.now(timezone.utc):
                parser.error('Measurement must have a timezone and cannot be in the future')
            record_sample(conn, args.used_bytes, args.limit_bytes, measured)
        elif args.command in ('pause', 'resume'):
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('research-query-admission'))")
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('research-dispatch-admission'))")
            conn.execute('UPDATE research.dispatch_control SET paused=%s WHERE singleton', (args.command == 'pause',))
        report = conn.execute('''SELECT paused,
            (SELECT count(*) FROM research.dispatch_outbox WHERE state='pending') AS pending,
            (SELECT count(*) FROM research.execution_reservations WHERE state IN ('launching','uncertain','running')) AS outstanding,
            (SELECT measured_at FROM research.capacity_sample WHERE singleton) AS capacity_measured_at
            FROM research.dispatch_control WHERE singleton''').fetchone()
    print(json.dumps(report, default=str))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Connection errors may embed credentials. Keep the operator log sanitized.
        print(json.dumps({'status': 'failed', 'category': type(error).__name__}))
        raise SystemExit(1)
