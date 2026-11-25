# Managed database and private operator Jobs

These commands implement the local migration path. No managed provider has been
qualified and no cloud recovery result is implied by the local PostgreSQL tests.
Cloud schedules stay paused until the chosen provider, cost review, real R2 drill
and delivered notification test pass.

## Database setup and permissions

Use PostgreSQL 17. Set `RESEARCH_OPERATOR_DATABASE_URL` to a direct endpoint with
`sslmode=verify-full`, a trusted CA, and hostname validation. Set
`RESEARCH_OPERATOR_ENDPOINT_MODE=direct`. Transaction-pool endpoints are not
supported for migrations, exported snapshots or the shared operator lock.

First record the empty candidate database's provider-owned baseline, review it,
then apply the managed migration path:

```sh
python -m deploy.managed baseline --output /private/provider-baseline.json
python -m deploy.managed migrate --baseline /private/provider-baseline.json --output /private/migration.json
python -m deploy.managed audit --output /private/qualification.json
```

Migration consumes five separate `RESEARCH_API_PASSWORD`,
`RESEARCH_WORKER_PASSWORD`, `RESEARCH_DISPATCHER_PASSWORD`,
`RESEARCH_IMPORTER_PASSWORD`, and `RESEARCH_BACKUP_PASSWORD` values from the
operator's secret bindings. Existing login passwords are preserved on repeat deployment.
After a fresh-cluster restore, run this same explicit setup command to activate
the restored disabled login roles with the isolated environment's secrets.
The managed ledger checksums the explicitly translated SQL; it is intentionally
incompatible with silently treating a VM migration ledger as a managed ledger.
Original SQL checksums are also captured in backup manifests.

| Identity | Access | Connection cap |
| --- | --- | --- |
| API | Published reads, quota counters, saved-query/subscription state, dispatch intent | 8 |
| Worker | Published solver input, fenced query/result execution state | 4 |
| Dispatcher | Queue and execution reservation state; no source-table writes | 4 |
| Importer/operator | Application data writes; no role/schema ownership or administrative attributes | 4 |
| Backup reader | All application rows, sequences and migration ledger; SELECT only | 4 |
| Restore/migration owner | Separate deployment identity; owns only the new application namespace | Reserved administrator capacity |

The runtime maximum is 24 connections; reserve at least eight additional
administrative connections. Test the actual direct and pooled endpoints under
exhaustion; these SQL limits do not establish provider plan capacity.

Generic VM roles map explicitly to `research_visitor`,
`research_authenticated`, and `research_importer`. The setup never edits provider
`anon`, `authenticated`, `service_role`, storage schemas, or extensions. A first
setup rejects pre-existing application roles/namespaces. Existing provider roles
and schemas are compared before and after migration. Import uses ordinary INSERT
statements because PostgreSQL COPY FROM cannot enforce RLS for a non-bypass login.

The backup reader has an unconditional SELECT policy on every application RLS
table. A managed backup refuses a missing policy, restrictive SELECT policy, or a
different/administrative reader. It uses `pg_dump --enable-row-security` only with
this all-row contract. It hashes every application table, including unpublished
rows, and retains separate complete-result hashes. Compare reader hashes against
the migration owner during provider qualification.

The audit reports allocated source/evidence/index/result/definition sizes,
application roles and provider baseline. Provider-billed history, branches,
network costs and a clean intended-scope import remain separate measurements.
`qualified` stays false until the report's pending checks are supplied.

## Operator execution

Each invocation runs one bounded command:

```sh
python -m ops.serverless backup
python -m ops.serverless verify
python -m ops.serverless prune
python -m ops.serverless cleanup
python -m ops.serverless import
python -m ops.serverless restore
```

Use the backup reader database secret for backup/verify/prune, the importer secret
for import/cleanup, and an isolated restore-owner secret for restore. The API and
worker receive no object credentials.

Required storage bindings are `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, and `RESEARCH_BACKUP_PREFIX`. Object operations additionally
use independent `OBJECT_R2_ENDPOINT`, `OBJECT_R2_BUCKET`,
`OBJECT_R2_ACCESS_KEY_ID`, `OBJECT_R2_SECRET_ACCESS_KEY`, and
`RESEARCH_OBJECT_PREFIX`. Reports use `RESEARCH_REPORT_PREFIX` with the backup
credential. Prefixes must not overlap in the same bucket. Credentials should be
restricted to their required buckets; prefix checks are application controls.

Set `RESEARCH_OPERATOR_MAX_STAGING_BYTES` below the Job's memory allocation after
reserving application and PostgreSQL client memory. Backup additionally requires
`RESEARCH_OPERATOR_MAX_DUMP_BYTES` and `RESEARCH_APP_COMMIT`. The complete object
inventory plus the dump reservation and 16 MiB metadata reservation must fit
before any payload is staged. Linux enforces a dump-file size limit; oversized
streams stop before excess bytes are written. The default overall operation
duration is 1500 seconds (`RESEARCH_OPERATOR_TIMEOUT_SECONDS`). Platform retries
must be zero; retries create a new immutable backup identity. Temporary directories
are removed on normal exit and are discarded by Cloud Run after termination.

Backup, pruning, import and cleanup hold the same database session advisory lock
for the entire operation. Termination releases the lock; object operations stop
when its connection is lost. The existing VM query-artifact cleanup uses the same
lock name. Do not run a second independent cleanup implementation or transaction
pool these endpoints. Completed backup markers are written last. Retention keeps
seven daily and four weekly sets, verifies the newest before deleting anything,
and removes an old completion marker before its payload. Interrupted deletions
leave incomplete remnants, not a falsely usable set. Removing abandoned partial
sets is a separate reviewed maintenance task.

Result cleanup retains any planned upload whose object is absent, because a timed-out
write may still finish. When that object exists and its full size and hash match,
cleanup commits the verified intent and defers its generation until the next cleanup
invocation. This separate pass preserves completion proof if a later deletion loses
its acknowledgement or its database transaction rolls back. Storage reservations are
released only after the expired terminal run has no active execution or retained result
generation, including runs that failed before producing their first result object.

Verify defaults to the newest complete set; `RESEARCH_BACKUP_ID` selects an exact
set. Import takes a private directory prefix in `RESEARCH_IMPORT_BUNDLE_KEY`
containing `bundle.json` and `artifacts/`, plus
`RESEARCH_IMPORT_RESTORED_BYTES` for the SQLite decompression reservation and
`RESEARCH_IMPORT_VERIFY_BYTES` for bounded logical-hash scratch space. Verification
streams rows through a size-limited SQLite index, preserving the original logical
hash without copying full tables into Python memory. It
verifies immutable uploads through the R2 API; no filesystem mount or hard link
is required.

Reports are immutable JSON objects in the report namespace and sanitized
structured stdout. Failures contain an exception category, never an exception
message or connection string, and exit nonzero. The configured Job-failure/log
alert is the independent notification path when R2 itself fails. A notification
channel and actual delivery test are still required before schedules are enabled.

## Isolated restore

Restore requires an empty application namespace in a distinct database ending
`_restore_test`, a separate restore identity, the reviewed provider baseline in
`RESEARCH_RESTORE_BASELINE_JSON`, a distinct `RESEARCH_RESTORE_OBJECT_PREFIX`,
and `RESEARCH_BACKUP_ID`. Also bind `RESEARCH_BACKUP_DATABASE_URL` to the source's
direct backup-reader endpoint, so retention cannot remove the selected set during
retrieval. Existing application schemas or ledger state reject
aliases of an active database. Provider baseline objects remain untouched.
VM backups require an explicit provider-role conversion rather than automatic
role adoption. Application role memberships are created for new roles; incompatible
existing roles fail without being weakened.

Restore verifies every application-table hash and complete-query hash before
fencing old leases and cloud execution bindings, changes the restored environment
identity, and pauses dispatch. Queued identities receive a new generation while
preserving attempt budgets. Interrupted/exhausted searches become incomplete with
an actionable saved-query recompute message; completed results remain intact.
Capacity measurements are cleared until the isolated provider is measured.
It never contacts the live dispatch queue. Object bytes are verified into the
isolated namespace. The returned `api_and_worker_verified=false` is intentional:
start separately configured isolated services, verify browser behavior, and record
retrieval-to-working-service duration to establish the two-hour recovery target.

## Local evidence and remaining hosted work

On November 25, 2026, fresh local PostgreSQL 17.11 managed setup and a repeat
setup passed. Backup/serverless tests: 33 passed plus 11 subtests. A real custom
dump using the NOBYPASSRLS reader restored into a separate local database and
matched all 75 application-table hashes, including an unpublished draft row.
The fixture dump was 359,980 bytes; this small fixture is not a production sizing
measurement. SQL role audit reported no runtime owner/admin grants or backup
write grants. Cross-connection operator contention and connection termination
released the shared lock as expected.

Run `pytest ops/test_backup.py ops/test_serverless.py`; set
`RESEARCH_MANAGED_TEST_DATABASE_URL` only to an isolated, already migrated
`*_test` database to include permission/lock integration tests. A managed test
cluster must be distinct from the VM-role fixture cluster.
The sanitized [verification record](../verification/serverless-managed-recovery.json)
contains every compared table hash and the dump/restore options.

Still pending: actual provider permission/endpoint tests, clean publication
import sizing, hosted latency/exhaustion, memory-kill/restore drill on Linux Cloud
Run, real R2 round trip, provider baseline review, notification delivery, 24-hour
idle observation and cost reconciliation. Local results do not complete SL-02,
SL-10 or SL-11.

Permission behavior was checked against the [PostgreSQL 17 pg_dump
documentation](https://www.postgresql.org/docs/17/app-pgdump.html). Endpoint
constraints follow [Neon connection pooling
documentation](https://neon.com/docs/connect/connection-pooling).
