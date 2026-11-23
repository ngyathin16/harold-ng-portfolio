# Managed data publication

The importer copies the source database into normalized Postgres tables. It preserves all 58 source tables and 17 research views for schema version 1. Integer flags remain integer flags, source JSON remains its original text, real values become `double precision`, and source identifiers remain unchanged. Every primary key and foreign key includes publication, dataset, and snapshot scope. Translated views join within publication scope and run with the caller's row policies.

Install the service requirements, then apply these migrations once, in order, using a privileged **server-side** connection:

1. `migrations/001_publications.sql`
2. `migrations/002_source_tables.sql`
3. `migrations/003_source_views.sql`
4. `service/migrations/002_queries.sql`
5. `migrations/004_storage.sql` on Supabase only
6. `migrations/005_service_roles.sql`
7. `migrations/006_private_artifacts.sql`

Postgres 15 or newer is required for `security_invoker` views. Local Postgres needs `anon`, `authenticated`, and `service_role` roles before migration; Supabase provides them. Never grant visitors writes. The service uses explicit publication checks in addition to these policies. The `research-artifacts` bucket stays private. Migration 006 removes its legacy visitor read policy and anonymous artifact metadata grants; only operators can access recovery objects. Use a direct or session-pool connection for import, with enough headroom for one bounded batch import; the request service uses its own bounded pool.

Prepare a portable, verified bundle without any credentials or network requests:

```powershell
python data/import_snapshot.py prepare --output .local/bundle
python -m unittest discover -s data -p test_import.py -v
```

`--snapshot` pins a retained snapshot. Defaults resolve `latest` once, then freeze that ID. The source snapshot must pass SQLite integrity and foreign-key checks, match the versioned schema mapping and every declared row count, and have a passing validation report. Every exported artifact must match its source manifest checksum. The bundle contains deterministic gzip of the original SQLite bytes, the original manifest, coverage and validation reports, and all declared table exports and references. Its metadata contains full-table row counts and logical hashes, artifact sizes and hashes, and the restored SQLite filename and hash. Local raw captures, `.env`, and credentials are excluded.

Publish with `DATABASE_URL`, `SUPABASE_URL`, and `SUPABASE_SECRET_KEY` supplied to the trusted import process:

```powershell
python data/import_snapshot.py publish .local/bundle
```

Before public release, review the source reuse conditions recorded in `tft-set-19/docs/sources.md` and the bundle's source manifest. This adapter publishes the complete source snapshot with attribution. It rejects table exclusions: a reduced public dataset needs a separately validated derivative database and mapping so that the API, roster, evidence and private recovery bundles cannot disagree about scope.

For private local integration verification, use `--local-storage .local/objects`. This stores objects on disk and is not a production storage backend. Both storage backends verify immutable bytes after upload. A failed upload prevents publication. Rows load in one transaction, with deferred scoped foreign keys checked before publication. Every Postgres table **and translated view** is read back and compared against its SQLite logical rows. Evidence source references are checked. Only then does the transaction publish the version and advance the dataset pointer. Failure rolls back all rows; an earlier publication stays readable. Repeated imports verify objects and reuse the identical publication without duplicates or moving the latest pointer backwards.

The mapping is deliberately versioned in `schema-v1.json` and the SQL migrations. A changed source schema fails with the affected table names; create and review a new migration instead of accepting unknown columns. `data/schema.py` generates the table migration; the view translations are explicit. Source-table JSON fields are not promoted into an opaque replacement for the relational model.

To test a second dataset through the shared browser, prepare the same schema fixture with `--dataset fixture-two --name 'Fixture dataset' --adapter generic`. It advertises browsing, tables and evidence, and has no TFT team-query capability. New source schemas require a deliberate mapping and migration.

CSV files retain the collector's encoding: exact `\N` means null; then replace doubled backslashes with one. Empty text remains empty text. Gunzip `tft-set-19.sqlite.gz` to restore `tft-set-19.sqlite`, and compare its SHA-256 to `restored_sha256` before using it. Preserve the source snapshot and publication IDs in citations.

Design references: [Postgres view privileges](https://www.postgresql.org/docs/current/sql-createview.html) and [Supabase private storage policies](https://supabase.com/docs/guides/storage/security/access-control).

## Growth and restoration verification

`python data/growth_fixture.py` only prints a plan. Its default is ten source-sized relational scopes, or 1,568,680 source table rows for the currently prepared bundle. It never selects a target from `DATABASE_URL`.

To execute, create an **empty dedicated database** whose name ends in `_growth` or `_test`, set `RESEARCH_GROWTH_DATABASE_URL` to its operator connection, then run:

```powershell
python data/growth_fixture.py --execute --output .local/growth
```

The target must have no `research` schema; the command refuses an existing application database. It applies migrations, imports the real baseline bundle, and creates nine additional distinct snapshot/publication scopes. Each copied table and view is recounted and compared with its source logical rows. Snapshot identity fields deliberately differ. It checks aggregate table counts, runs `ANALYZE`, and records four representative `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans plus table/index sizes. The fixture scopes are labeled synthetic and omit duplicate downloads; this exercises relational volume, not storage egress or public release scope. The target database remains for inspection. A single plan per read is not a hosted latency percentile or a concurrency measurement. Use the separate HTTP load harness for that workload.

Back up the database and objects to separate protected locations. A concrete local restoration rehearsal using PostgreSQL client tools is:

```powershell
pg_dump --dbname $env:DATABASE_URL --format custom --file .local/backup/research.dump
# Copy the private object directory separately, preserving every storage_key path.
# For Supabase, independently export the bucket objects using trusted operator access.
createdb --maintenance-db $env:RESEARCH_MAINTENANCE_URL research_restore_test
pg_restore --dbname $env:RESEARCH_RESTORE_DATABASE_URL --exit-on-error .local/backup/research.dump
python data/verify_restore.py --objects .local/restored-objects --output .local/restore-report.json
```

Create the parent backup directory first. Set `RESEARCH_RESTORE_DATABASE_URL` to the newly created isolated database and `RESEARCH_MAINTENANCE_URL` to that test cluster's maintenance database. Restore the separately backed-up objects into `.local/restored-objects`; do not point the verifier at the original object store when claiming a completed recovery drill. A restore into a different cluster needs the original no-login role definitions before `pg_restore`, because ordinary database dumps do not contain global roles. Never use a production database as the restore target, and do not pass `--clean` against an existing application database.

`verify_restore.py` opens a repeatable-read, read-only transaction. It checks each restored table/view against retained logical hashes, every registered source and query object against size/SHA-256, gzip restoration against the SQLite hash, and saved queries against their original publication/snapshot/ruleset. It performs no restore itself. Record the dump start, restore finish, object-copy duration, database/object backup age and the resulting recovery time and loss window. Then point an isolated API at the restored database/object store and reopen a saved record and query URL. Passing this verifier against the current database is only a verifier smoke test, not evidence of backup restoration.
