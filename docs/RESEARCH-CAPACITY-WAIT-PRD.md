# Research workspace readiness while compute capacity is unavailable

Product requirements document. Version 1.0. November 23, 2026, Asia/Hong_Kong.

Status: implemented and verified for the capacity-wait phase on November 23, 2026. See [the implementation record](RESEARCH-CAPACITY-IMPLEMENTATION.md) for evidence and remaining hosted launch checks. This document retains the approved requirements.

## Purpose

Use the wait for Oracle compute capacity to prepare a repeatable deployment, implement private R2 recovery, remove public research downloads, and verify the existing application and statistics locally. A change of server provider must not require replacing the database, search worker, or backup format.

The owner is the operator and primary researcher. Visitors retain public browsing, evidence, statistics, bounded team searches, and reproducible links. Database backups and exported research files remain operator-only.

## Decisions and scope

| Decision | Requirement |
| --- | --- |
| Compute | Prepare a Linux server running PostgreSQL, the API, and a separate team-search worker. Oracle Always Free remains the first deployment choice while capacity is unavailable. |
| Storage | Use private Cloudflare R2 for off-server backups and required recovery objects. R2 does not run PostgreSQL or the search worker. |
| Portability | Keep provider identifiers and network settings separate from the service setup. Support an alternative Linux VM without changing application or backup semantics. |
| Public access | Keep the browser application and bounded read/query APIs. Remove public database, table, report, and team-result downloads, including streamed exports. |
| Cost | Target no additional recurring hosting cost. Preparing this work does not authorize a paid server, paid plan, or a new spending ceiling. Measure retained bytes and storage operations before scheduling backups. |
| Delivery | Complete and test the work that does not need a production VM. Record hosted and native ARM checks as pending when the required environment is unavailable. |

This PRD supersedes conflicting download and provider requirements in the application PRD, hosting notes, and earlier acceptance criteria:

- R7 retains citations and copied links; public download requirements are withdrawn.
- R8 retains atomic publication and recovery. Private recovery objects do not need a visitor-facing download service.
- R11 and AC-17 retain exhaustive result validation. Independent database recounts replace public export comparisons.
- AC-08 retains citations and reproducible links. Artifact integrity moves to private backup and restore checks.
- AC-15 and public download journeys become removal checks. The portfolio's unrelated resume and project downloads remain supported.
- Supabase is optional. The deployment baseline is self-managed PostgreSQL, with private R2 backups independent of the compute provider.

All other research semantics, snapshot isolation, bounded reads, public query limits, and hosted release requirements remain applicable. Removing download buttons does not prevent visitors from retaining data returned by public APIs. This phase does not make published research confidential.

## Existing foundation

The implementation record identifies `feature/research-workspace` in the existing portfolio repository as the publication branch. Its backend is in `projects/research-workspace/`; the research workspace also has a local development copy in `research-app/`. Keep their intended changes aligned without overwriting unrelated work.

| Area | Recorded baseline | Work still needed |
| --- | --- | --- |
| Deployment | A Python service Dockerfile, SQL migrations, separate API/worker roles, and hosting notes exist. | A complete, repeatable server setup with persistent storage, ingress, scheduled operations, restart, and rollback. |
| Cloud access | The November 23 record reports successful Oracle authentication and private R2 upload/read/delete checks. Oracle A1 capacity was unavailable in Singapore at that check. | A real R2 backup and restore drill. An access test is not recovery evidence. |
| Recovery | A local PostgreSQL dump/restore rehearsal is recorded. `data/verify_restore.py` verifies restored logical rows, objects, and saved query identity. | Automated backup transport, retention, failure handling, isolated recovery, and measured recovery targets. The verifier itself does not restore anything. |
| Downloads | The API, UI, and worker support source artifacts and complete query exports, including a streaming fallback. | Remove every public delivery path and unnecessary automatic export generation. |
| Statistics | Existing records and tables expose unit stats, observations, coverage, and team-result counts. | Verify these values and their interpretation. A new statistics dashboard or match collector is outside this phase. |

Recorded local checks provide a starting point, not evidence for the new behavior. Credentials already exist outside Git; this work does not require collecting them again or copying them into documentation.

## W1. Reproducible server setup

Deliver a version-controlled deployment package and operator runbook. Prefer a small container composition with a bootstrap command over a new orchestration platform.

1. Declare a supported Linux baseline, PostgreSQL major version, runtime versions, and immutable image references. Use PostgreSQL 17 and the existing Python 3.12 runtime as the initial compatibility baseline. Record dependency and migration versions.
2. Configure PostgreSQL, API, worker, HTTPS reverse proxy, cleanup, and backup scheduling independently. Keep database storage and any retained object directory on persistent volumes.
3. Supply a placeholder-only environment template. Validate required configuration before starting services. Keep credentials out of images, logs, shell traces, browser assets, and Git.
4. Create the compatibility roles needed by plain PostgreSQL. Apply the existing migrations in order, skipping the Supabase-only storage migration. Record applied migrations, prevent concurrent application, and make repeated setup safe.
5. Give the API, worker, importer, and backup operation only their required database permissions. Do not expose the PostgreSQL port publicly. Restrict SSH to the configured operator access; expose application traffic through HTTPS with explicit proxy trust and origin settings.
6. Configure health checks, startup ordering, automatic restart, log rotation, and scheduled cleanup. Separate database connectivity from worker readiness so the operator can identify a missing worker.
7. Make concurrency, queue size, result retention, memory, CPU, and disk thresholds configurable. Start conservatively and measure contention between reads, searches, and backups. Admission must stop safely before disk exhaustion without deleting source publications or frozen rules.
8. Provide a deployment sequence for configuration, start, migrations, snapshot import or restore, health checks, and browser verification. Provide a compatible application rollback that preserves database volumes and never restores public download access.
9. Build for the target CPU architecture. Exercise x86 locally where available and record native ARM dependency, Numba compilation, lease, and runtime checks as pending until ARM compute is available. Emulated builds alone do not establish ARM performance.

Acceptance: a fresh disposable Linux environment starts from the documented inputs; a second setup run causes no duplicate migrations, roles, or data. Restarting the services preserves a selected publication and saved query. A failed migration stops deployment without advancing publication state. Record the commit, images, architecture, commands, timings, and results.

The completion goal is a documented setup with no ad hoc application edits after a host becomes available. No deployment-time promise is made before the rehearsal is measured.

## W2. Private database backup and restore through R2

### Backup contents and consistency

Deliver operator commands for backup, list, verify, restore, and retention cleanup. Each command must return a failure status when its required checks fail.

Each backup set must include:

- A consistent PostgreSQL custom-format dump containing publications, source relations, evidence, statistics, frozen rules, saved query definitions, and the result state covered by the dump.
- The required immutable source bundles and other registered private objects, with their original keys and hashes. Existing retained query objects remain recoverable until explicitly retired by a compatible migration.
- A manifest containing backup ID, format version, UTC start and finish times, database and client versions, application commit, migration version, publication identities, object inventory, byte sizes, and SHA-256 hashes.
- Non-secret role and grant definitions needed to recreate the database in a fresh cluster. Supply fresh login passwords through the secret configuration during recovery.

`pg_dump` captures a consistent database snapshot, but database dumps do not include cluster-wide roles or filesystem objects. Package those dependencies separately. Use a compatible client and restore target. [PostgreSQL SQL dump documentation](https://www.postgresql.org/docs/current/backup-dump.html)

Define one consistency boundary for the dump and object inventory. Use a shared database snapshot, or pause every writer that can change object references while capturing both. This includes publication, worker writes, object cleanup, and API retries or cancellations that retire artifacts. A bounded pause is acceptable for this phase; ordinary reads can continue. Protect referenced immutable objects from deletion until copying and verification finish. Never combine an old dump with an unchecked listing of the current object store.

Write backup sets under immutable IDs. Upload the manifest and completion marker only after all members pass size and SHA-256 verification. Interrupted or partial sets must never appear as the latest successful backup. Retry safely without overwriting a completed set or pruning the last usable backup. Do not use an object-store ETag as the required content hash.

### Privacy and retention

Keep the bucket private, with public `r2.dev` access and public custom domains disabled. Use bucket-scoped operator credentials for the S3-compatible API. The public API and browser must have no backup credentials or route that returns a backup object or signed backup URL. R2 supports private buckets and scoped object permissions. [R2 public access](https://developers.cloudflare.com/r2/buckets/public-buckets/), [R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)

Use TLS for transfer. Restrict local dump and restored-file permissions, and keep them out of source control. Persist a sanitized operation report without credentials, connection strings, or signed URLs.

Proposed operating defaults for this phase are a daily backup, seven daily sets, and four weekly sets. A set can satisfy both retention categories. Keep at least one fully verified, restorable set. Retention must protect every object referenced by any retained manifest, stay inside the configured backup bucket/prefix, and support a dry run. Measure bytes and request counts before enabling the schedule; record any resulting cost constraint.

Proposed recovery targets are at most 24 hours of database data loss and at most two hours to restore the current dataset on a prepared compatible host. Measure the loss window from the recovered dump's snapshot time. Measure recovery time from starting the R2 retrieval through a working isolated API and worker. Server procurement time is separate. Record actual results and failures against these targets; they are not existing guarantees.

Track the most recent successful backup, backup age, duration, bytes, verification status, and most recent restore drill. A failed upload or expired credential must be visible to the operator, leave the previous backup usable, and never be reported as success. Specify the operator's failure-notification mechanism before enabling unattended backups.

### Restore drill

Restore only into an explicitly selected, empty isolated database and object directory by default. Reject the active application target. Replacing production requires a separate, deliberate recovery procedure.

Retrieve a named completed backup from R2 into a fresh location. Verify every member against the manifest before using it. Recreate roles, restore the database and objects, and run the existing restore verifier against those restored copies. Extend its checks where the retired public-export model changes the required object inventory.

Start an isolated API and worker against the recovered state. Reopen a record, a statistics view, and a saved query in the original publication and ruleset. Check representative source and result counts independently. Treat restored active jobs as interrupted work; expire stale leases and use the normal retry rules without duplicate results or workers attached to the live queue. Keep the drill's scheduler disabled so it cannot overwrite operational backup status or prune retained backups.

Acceptance: complete one real R2 round trip from the local database into a fresh local restore target without Oracle compute. The original database and object directory must not supply reads during verification. Test missing objects, corrupted bytes, interrupted uploads, inaccessible credentials, retention dry runs, and an unsafe restore target. Anonymous object requests and all public application routes must fail to retrieve the backup. Record measured recovery time, backup age, and any unmet target.

## W3. Remove public research downloads

Apply the change across the frontend, API, worker, schemas exposed to visitors, tests, and documentation. Preserve operator recovery and private local research exports.

| Area | Required behavior |
| --- | --- |
| Interface | Remove the research Downloads destination, database/table/report download links, query CSV/JSON buttons, and export preparation notices. Old download tab URLs resolve to a useful research page without reviving download controls. |
| API routes | Retire publication download listings, artifact delivery/signing routes, and `/v1/queries/{id}/export` for every format. Direct GET and HEAD requests must return 404 or 410, never bytes, a signed redirect, or a complete stream. |
| Public responses | Remove export URLs, storage keys, and export-preparation fields that exist solely to support downloads. Remove the retired routes from the active API contract. Keep publication metadata and evidence needed for research. |
| Worker | Stop automatic public JSON/CSV export creation after query completion. A correctly completed search must remain complete without an export artifact or storage connection. Preserve private backup inventory and safe cleanup of already retained objects. |
| Static assets and storage | Verify that no research archive or backup is bundled into the public site or exposed through a static directory. If older URLs or cached responses were deployed, retire them and verify expiry or invalidation before release. |
| Browsing and citations | Keep paginated catalog, table, and result access, copied record/query links, citations, and snapshot/ruleset provenance. Preserve the existing page-size limits. |

Do not delete historical source bundles or the only recovery copy as part of UI removal. Any retirement of stored export objects must follow the private backup inventory and retention rules. Do not remove unrelated portfolio downloads.

Acceptance: no research download action appears at desktop or mobile sizes. Test known old artifact URLs, download lists, both query export formats, and storage access without credentials. None may return a research file, stream, or signed URL. A full query completes and its paginated results remain accessible while export storage is unavailable. Existing copied record/query links, citations, and unrelated portfolio download journeys pass.

## W4. Continue local application and statistics verification

Use real local PostgreSQL, the API, a running worker, and the portfolio UI for integration checks. Small fixtures should cover failure and interpretation cases. Label skipped database tests explicitly; a mock-only pass is insufficient.

| Verification area | Required coverage |
| --- | --- |
| Application journeys | Dataset selection, catalog search and filters, record details and evidence, table pagination beyond 200 rows, snapshot changes, deep-link reload, browser Back, and copied citations. |
| Team queries | General Jhin preset and its optional Jazz constraint, contradictory filters, duplicate-query reuse, cancellation ownership, worker restart, expiry, retry, and accurate complete/partial/empty states. Compare optimized results with an independent small-roster oracle and recount representative full runs. |
| Unit statistics | Display stored coordinates and units correctly. Preserve unknown, zero, false, and empty values. Do not invent interpolation or assume every coordinate is a star level. |
| Observation statistics | Keep sample counts, composition groups, unclassified boards, population totals, denominators, source scope, and observation windows distinct. Show a known zero sample count as zero; a rate with a zero denominator is unavailable, not 0%. Do not relabel a composition aggregate as a champion-level win rate. |
| Derived counts | Verify configuration count, distinct champion-set count, active-trait count, total cost, and coverage counts against independent calculations. Counts must cover the selected scope, not only the visible page. |
| Version and evidence | Pin every statistic to its publication/snapshot and relevant ruleset. PBE data, unknown dates, unresolved conditions, and archived evidence retain their limitations. A requested observation window must not become an invented verified date range. |
| Usability and failures | Keyboard access, 1280-pixel and 390-pixel layouts, 200% zoom, empty states, API/database unavailability, request errors, and worker absence. An outage must not appear as zero observations or zero matching teams. |

Use the existing data, solver, service, and browser suites as the baseline. Update download assertions to check removal. Run the portfolio build and applicable release checks after interface changes. Add tests only for behavior or failure cases that the existing suites do not cover.

For local performance runs, record CPU architecture, available memory, software versions, snapshot, normalized queries, solver version, cache state, elapsed time, peak memory, database bytes, and errors. Separate cold compilation from warm execution. Local results do not satisfy the existing hosted concurrency or latency gates.

Acceptance: all applicable local journeys and statistics checks pass, failures are fixed, and the verification report lists the exact commands, commit, fixtures, results, skips, and remaining hosted checks. Do not describe an unrun test as passed.

## Delivery order and reviewable outputs

| Stage | Work and dependency | Reviewable output |
| --- | --- | --- |
| 1 | Start server packaging and public-download removal in parallel. Inventory the recovery data before deleting any export behavior. | Deployment configuration, placeholder environment template, removal changes, and route regression checks. |
| 2 | Implement private R2 backups against the retained data contract. No Oracle VM is needed. | Operator commands, manifest format, retention policy, and backup failure reports. |
| 3 | Perform R2 restore and local regression checks on the combined changes. | Restore report, independent integrity results, statistics checks, and browser evidence. |
| 4 | Rehearse deployment in a fresh local Linux environment and assemble the handoff. | Setup/rollback runbook, pinned release inputs, and a checklist of hosted checks still pending. |

Store reproducible scripts and sanitized reports in the application repository. Keep credentials, dumps, restored objects, and raw private diagnostics outside Git.

## Completion and later deployment

The capacity-wait phase is complete when W1 through W4 pass all checks possible without production compute. This includes an actual private R2 restore, removal of every public research download path, and passing local statistics and browser journeys. A native ARM check may remain pending if no ARM environment is available; state that limitation in the handoff.

Once capacity appears, recheck the proposed allocation's free eligibility, provision the selected host, and apply the prepared setup. Then verify native architecture behavior, HTTPS, private network/storage policies, backup scheduling, an isolated hosted restore, disk admission, and the original uncached-query/concurrent-browsing targets. Keep production readiness pending until those checks pass.

Final host capacity, API hostname, measured worker concurrency, retention cost, and source publication scope remain launch decisions. No new dashboard, new match-data collection, authentication product, infrastructure migration, or paid purchase is required to finish this phase.
