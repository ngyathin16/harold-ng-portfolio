# Private R2 backup and restore

Run these operator commands from `projects/research-workspace/`. They never publish a browser download or signed URL. Install `service/requirements.txt` and `ops/backup-requirements.txt`. PostgreSQL 17 client tools must be on `PATH`, or set `PG_BIN` to their directory. Use a client compatible with the source server.

## Configuration

Supply secrets through the operator process environment or the deployment secret files. The commands do not source `.env` files. Never put connection strings or secrets in command arguments or `PUBLIC_` variables.

| Variable | Purpose |
| --- | --- |
| `RESEARCH_BACKUP_DATABASE_URL` | Source database. Its dedicated login needs schema usage, SELECT on all application tables/sequences and the migration ledger, plus BYPASSRLS. It does not need write permissions. |
| `RESEARCH_ARTIFACT_ROOT` | Absolute private directory containing registered immutable objects, including source bundles and any retained query exports. |
| `RESEARCH_APP_COMMIT` | Full 40-character Git commit for the application that produced this backup. |
| `R2_ENDPOINT` | HTTPS R2 S3 endpoint. |
| `R2_BUCKET` | Existing private backup bucket. |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Bucket-scoped operator object credentials. Keep them off API and worker containers. |
| `RESEARCH_BACKUP_PREFIX` | Backup namespace; default `research-backups`. Use a separate prefix for drills. |
| `RESEARCH_RESTORE_DATABASE_URL` | Operator connection to an existing, empty isolated database ending in `_restore_test`. It must differ from the source database name. |

Keep `r2.dev` and public custom domains disabled in the R2 bucket configuration. Verify that configuration before scheduling backups; successful S3 authentication does not establish private public-domain settings. TLS is required by the CLI. [R2 public access](https://developers.cloudflare.com/r2/buckets/public-buckets/)

Use an operator-only working directory outside Git. On Linux, the CLI sets private directory/file modes and a restrictive umask. On Windows, create the working directory under the owner's private profile and restrict its NTFS ACL to the owner and SYSTEM before use; POSIX permission bits alone do not enforce Windows ACLs.

## Commands

```text
python -m ops.backup backup --work-dir /private/recovery --report /private/recovery/backup-report.json
python -m ops.backup backup --discard-staging --work-dir /private/recovery
python -m ops.backup list --work-dir /private/recovery
python -m ops.backup verify --backup-id BACKUP_ID --work-dir /private/recovery
python -m ops.backup prune --work-dir /private/recovery
python -m ops.backup restore --backup-id BACKUP_ID --work-dir /private/recovery
```

Each backup gets a unique ID. Its custom-format dump, NOLOGIN role definitions, required objects, and version/checksum manifest are self-contained. Every uploaded member is read back and hashed before the completion marker is written. An interrupted upload is not listed as complete. Retrying creates a fresh ID and never overwrites a completed backup. No mutable `latest` pointer can hide an older usable set.

For managed backups, `ops.serverless backup` streams each registered R2 source object into private staging and verifies its declared size and SHA-256. It retains that same GET response's strong ETag in memory, then uses R2 `CopyObject` to create a separate copy inside the new backup namespace. The request signs both `x-amz-copy-source-if-match` and `cf-copy-destination-if-none-match: *`. The latter prevents overwriting an existing destination. The backup credentials must read the source bucket and write the backup bucket on the same R2 account endpoint; unsupported conditions, missing ETags, or different account endpoints fail without uploading the source body as a fallback. The database dump and metadata still use conditional uploads.

Every copied destination is streamed back and checked against the original size and SHA-256 before completion. An ETag is a version condition, not a replacement for those integrity checks. Source receipts are not stored in the manifest, whose format remains compatible with existing restores. Sets have no shared object dependencies: deleting an original or another backup cannot break a completed set. Reports include `server_side_copied_objects` and `server_side_copied_bytes`; these are source bodies whose uploads through the operator were avoided, not total network-billing measurements.

R2's destination-copy conditions are beta as documented November 10, 2026. Source conditions are checked when selecting the source; destination conditions are checked at commit, so the two checks are not atomic together. Destination hash verification remains mandatory. Transport retries retain both conditions. If a committed copy loses its response and a retry returns 412, the operation fails without a completion marker; retry the backup with a fresh ID and inspect incomplete sets separately. Tests on November 25, 2026 used the pinned boto3 1.44.9 with botocore 1.44.9 and inspected actual SigV4 requests through a mocked HTTP transport. [R2 copy conditions](https://developers.cloudflare.com/r2/api/s3/extensions/#conditional-operations-in-copyobject-for-the-destination-object), [signed custom headers](https://developers.cloudflare.com/r2/examples/aws/custom-header/).

The [live synthetic-copy qualification](../verification/serverless-r2-copy.json) passed on November 25, 2026 with the same pinned SDK versions. It used a new unique namespace under `staging/copy-qualification-20261125/`, a maximum of 25 HTTP attempts, and a 4 KiB synthetic-storage limit. Actual usage was **19 HTTP attempts, zero transport retries, and 781 bytes across seven new objects**. The signed SDK copy succeeded; an existing synthetic destination returned 412 and retained its hash; a mismatched source ETag returned 412 with no destination created. `publish_set` verified copied bytes before completion, and a deliberately incorrect expected SHA-256 failed on live readback. The tiny fixtures remain for review; no pre-existing objects were modified or deleted. This qualifies the R2 copy path, not a PostgreSQL restore, deployed operator image, or monthly spending ceiling.

The database inventory and `pg_dump --snapshot` use the same exported repeatable-read snapshot. The exporter holds `research-backup-objects` as a session advisory lock until the required immutable objects have been copied and the dump has finished. Object cleanup must take the corresponding transaction lock through candidate selection, physical deletion, and unregistering. Ordinary reads and database writes can continue. The retained dump contains the snapshot's result state even if live queries finish later. [PostgreSQL pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html)

Migration file hashes identify the source package. `applied_migrations` records the deployment ledger when it exists; `null` explicitly means an older database has no ledger. The dump itself preserves the actual schema. Source table logical hashes, saved query identity, and per-query result-row SHA-256 checks provide independent recovery checks.

All commands return nonzero on failure. JSON output omits raw database, subprocess, and storage exception text because it can include credentials. Reports retain `last-COMMAND-attempt.json` separately from `last-COMMAND-success.json`. A failed backup therefore cannot replace the latest successful report. Reports are written while the operation lock is held; a competing command cannot replace them. `list` verifies manifest integrity and reports backup age and bytes; run `verify` for a full remote member check. The `r2_requests` count measures SDK operations; automatic transport retries can add billable requests. An orphaned `.backup-operation.lock` after a crash requires operator inspection before removal. Confirm no operation is running first.

## Retention and scheduling

The default dry run retains the newest set on each of seven distinct days and four distinct ISO weeks. A set can satisfy both rules. Run `prune --execute` only after reviewing the dry-run list. Retention verifies every member of the newest retained set before deleting anything. If it is corrupt or inaccessible, pruning stops. Complete sets have no shared object references, so deleting one cannot remove another set's dependencies. Deletion stays within the configured namespace and removes the completion marker first. Incomplete or interrupted deletions need a separate inspected cleanup; they cannot become complete again.

Measure retained bytes and S3 request counts before enabling the daily schedule. At most eleven distinct full sets are retained by the default categories. The CLI uses conditional single-object uploads and currently supports member files below the S3 single-PUT size limit; oversized files fail rather than silently using a partial backup. Review large-data growth before reaching that limit.

Manual backups retain staging for inspection by default. Scheduled backups must pass `--discard-staging` so each daily run does not retain another full database and object copy locally. This option verifies all remote members again, checks the exact local manifest and member hashes, and removes only declared payload files within that backup ID's directory. It rejects links, junctions, path escapes, and unexpected local files. Manifests and completion markers remain for inspection. It never removes restored directories, source objects, or other backup sets.

Failed staging remains for operator diagnosis and cleanup. Monitor working-directory free space and failures even with successful-stage cleanup enabled. Keep a restore target until its browser and worker checks finish. Scheduling must include the deployment failure-notification hook and a daily check for backup age over 24 hours. Until those are configured, run backups manually. A local log without an attended review is not an unattended alert.

## Isolated restore drill

Create a fresh database from `template0` with a name such as `research_capacity_restore_test`. Do not connect an API, worker, or scheduler yet. Configure `RESEARCH_RESTORE_DATABASE_URL` and run the restore command above. The tool rejects the source database name, non-test names, populated schemas, public tables/functions/types, and existing connected clients.

Restore first retrieves and verifies the full R2 set into a newly created directory. It never reads the original artifact directory. It creates missing roles as NOLOGIN without passwords or elevated attributes and restores captured memberships for newly created members. Existing cluster roles are unchanged. The dump preserves database grants; configure fresh login passwords, service memberships, and required BYPASSRLS attributes through the deployment process when recovering a fresh cluster. The CLI uses neither `--clean` nor database deletion.

`pg_restore` runs in one transaction with `--exit-on-error` and `--no-owner`; the chosen operator owns restored objects. The verifier checks source logical rows, all registered object hashes, saved definitions, retained complete-result counts, and the exact stored rows of every saved query against the backup manifest. It then expires restored running-job leases so the normal bounded retry path can fence and replace interrupted attempts. Other restored state remains intact.

The restore report records download-to-database-verification duration and the recovered snapshot's age. It sets `api_and_worker_verified=false`. Start an isolated API and worker on separate ports, point them only at the restored database and downloaded object directory, and reopen a record, statistics table, and saved query. Keep the scheduler disabled. Record the extra time through those successful requests before comparing with the two-hour recovery target. A 24-hour loss-window target starts at the dump snapshot, not upload completion. Procurement time is separate.

Run the deterministic failure checks with `python -m unittest ops.test_backup ops.test_backup_copy -v`. A real R2 round trip and restored API/worker exercise remain necessary; in-memory object-store tests do not establish recovery.
