# Capacity-wait implementation and verification

Started November 23, 2026, Asia/Hong_Kong. Baseline commit: `3265652`.

## Execution plan

- [x] Read the workflow principles and inspect the existing implementation and PRD.
- [x] Separate deployment, backup, and download-removal edits into independent Git worktrees.
- [x] Package repeatable Linux deployment, migrations, health checks, scheduling, and rollback.
- [x] Implement private R2 backup, verified restore, inventory, retention, and failure handling.
- [x] Remove public research downloads in the API, worker, interface, and browser checks.
- [x] Integrate the changes and verify against local PostgreSQL, a real R2 round trip, and the browser.
- [ ] Rehearse a fresh Linux deployment and repeat setup/restart checks where the local runtime permits.
- [ ] Review security and recovery boundaries, align local source copies, and publish validated changes.

Completion requires observed behavior against the real services. Unit tests alone do not establish recovery, deployment, or statistics correctness. Hosted HTTPS, Oracle capacity, and native ARM performance remain separate launch checks.

## Baseline and constraints

The existing checkout is clean. Historical verification covers the previous download-enabled implementation. Docker is installed, but its Linux engine was not running at the first check. Runtime availability is being investigated before selecting the Linux rehearsal environment.

The work is split across three independently editable units and one integration/verification stream. Database and backup changes receive failure-case and actual recovery checks because a successful command alone cannot establish recoverability. No paid infrastructure or production deployment is part of this run.

## Verification results

Local verification uses Windows 11 x86, PostgreSQL 17.11, and Python 3.12.13. The real source database remains unchanged. The recovery database and object directory are separate copies retrieved from R2.

| Check | Observed result |
| --- | --- |
| Importer and solver | 9 importer tests and 13 solver tests pass, including the real source bundle and snapshot. `RESEARCH_BUNDLE_ROOT` and `RESEARCH_SOURCE_PROJECT` select those private fixtures explicitly. |
| API and worker boundaries | 32 PostgreSQL-backed service tests pass, including retired GET/HEAD routes, no export dependency, private artifact access, low-disk admission, and backup/GC exclusion. |
| Operations | 35 tests pass, including 20 backup failure, integrity, retention, staging-containment, and lock/report cases. The existing capacity-monitor tests remain green. |
| Deployment logic | Six tests pass on the deployment worker's disposable PostgreSQL cluster. They exercise atomic failure, repeat setup, changed-migration rejection, role permissions, restored NOLOGIN accounts, exact legacy-schema adoption, and configuration. The integrated configuration checks pass; full Linux execution is pending the CI rehearsal. |
| Real private R2 recovery | 250 members totaling 295,297,091 bytes uploaded and read back for SHA-256 verification. A fresh restore verified 248 objects, 75 relations in each of two publications, three saved definitions, and three full result hashes. Database/object restoration took 98.667 seconds. This is not the full recovery-time measurement. |
| Recovery target | The conservative elapsed bound through restored-browser, worker, and statistics verification is recorded in `capacity-recovery.json`. It includes manual review and repeated measurement. It is below the proposed two-hour target. Backup age at restoration was below 24 hours. |
| Bucket privacy and retention | Authenticated bucket checks confirm disabled `r2.dev` access and no custom domains. Unsigned GET/HEAD requests returned HTTP 400 S3 errors, with no backup bytes. A real retention dry run reverified the newest set, retained it, and deleted nothing. |
| Restored worker | General and Jazz queries recomputed without artifact storage and exactly matched 280,543 and 38,809 configurations. Every result was independently recounted. The Jazz results are a subset of the general results. The saved query IDs remain unchanged. |
| Statistics | Public unit/observation rows match scoped SQL across all pages; observation formulas, coverage totals, distinct champion sets, and team aggregates pass independent checks. |
| Browser | Real research journeys pass with the restored database, including no downloads, saved queries, complete/empty/error distinctions, keyboard use, narrow layouts, enlarged text, and an automated WCAG scan. |
| Portfolio | 24 page/viewport checks, 60 local links, unrelated downloads, 20 design combinations, five no-JavaScript journeys, and navigation checks pass against the built preview. |
| Build/release | Build and type checks pass with no errors, warnings, or hints. First-party JavaScript is 84,431 bytes gzip under the 90,000-byte budget. All 21 release tests pass. |

The first worker run used an empty Numba cache; the second used the populated cache. Compilation time is not separately isolated. Memory is sampled process-tree RSS, excludes the database/API, and can count shared pages more than once. See the measurements and methods in [the worker report](../projects/research-workspace/verification/capacity-worker.json).

Evidence is retained in [the recovery report](../projects/research-workspace/verification/capacity-recovery.json), [statistics report](../projects/research-workspace/verification/capacity-statistics.json), [browser report](../projects/research-workspace/verification/capacity-browser.json), and [decision log](research-capacity-decisions.tsv). Raw dumps, credentials, and private staging remain outside Git.

## Reproduce and operate

Use [the deployment runbook](../projects/research-workspace/deploy/README.md) for fresh setup, migration/adoption, recovery startup, scheduling, and rollback. Use [the backup runbook](../projects/research-workspace/ops/backup-README.md) for private backup/list/verify/restore/prune commands. Schedules require a working operator notifier and cost review; none was enabled by this local implementation run.

From the backend directory, run the commands below with the documented isolated database variables. Real source fixtures are optional in clean CI and are explicitly selected for local verification.

```text
python -m unittest discover -s data -p test_import.py -v
python -m unittest solver.test_solver -v
python -m pytest service -q
python -m unittest discover -s ops -p test_*.py -v
python -m unittest deploy.test_deploy -v
python data/verify_statistics.py --api http://127.0.0.1:8789 --output verification/capacity-statistics.json
```

`data/verify_worker_recovery.py --execute` deliberately expires and recomputes only the two named queries in the isolated restore database. Install `verification/requirements.txt` for its memory measurement. It refuses the original database and requires a loopback API. The ordinary statistics verifier is read-only.

The existing GitHub research-checks workflow now includes backup/deployment tests and a fresh Docker/Linux rehearsal using synthetic data. `verification/verify_linux.py` builds the containers, repeats setup, runs a real query, checks private ports and retired routes, restarts the stack, and reopens the same record/statistics/query. It does not need R2 credentials.

## Remaining gates

Docker Desktop's Linux engine could not start locally. The owner authorized restarting WSL; its shutdown hung and the service restart failed because this session has no administrator rights. Linux container execution is being verified through the existing public repository's CI runner.

Native ARM execution, final HTTPS routes, production firewall/storage policies, unattended notification delivery, hosted recovery, concurrent hosted load, capacity, and final publication scope remain launch gates. No Oracle VM or paid resource was provisioned. Do not describe this as a production deployment.
