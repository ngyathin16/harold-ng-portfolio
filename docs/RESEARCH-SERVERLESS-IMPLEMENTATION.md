# Serverless implementation record

November 25, 2026, Asia/Hong_Kong. Implements the local migration and deployment package from [the PRD](RESEARCH-SERVERLESS-PRD.md). This is **not a production acceptance record**. No paid resources were created and no traffic or schedules were switched. The existing Oracle monitor and its expiry remain unchanged.

## Delivered behavior

Query acceptance commits the query, subscription, capacity reservation and dispatch outbox before returning. Cloud Tasks creation is a bounded, synchronous best effort after commit; scheduled reconciliation can recover missed wakeups. Cached queries keep their identities without new dispatch. PostgreSQL serializes queue admission, outstanding executions and launch-rate decisions across replicas.

The dispatcher launches one allowlisted Cloud Run Job with the query ID, generation and reservation ID. An execution can claim only that reservation. Unknown launch acknowledgements retain capacity; expiry never licenses a duplicate paid execution. Workers renew leases during callback-free phases, fence before enumeration and result writes, and atomically publish final counts. Platform retries are disabled; application attempts and launch attempts are finite. The VM worker remains available for the existing deployment.

`/live` performs no database work. `/health` and `/ready` check the database without writing visitor quotas; readiness reports stalled current-generation work. Cloud Run ignores caller-supplied forwarding headers and uses a shared network quota bucket across replicas and hostnames. This prevents spoofing but groups unrelated visitors together until a trusted hosted ingress path is qualified. Session ownership, pagination, frozen solver rules and retired GET/HEAD exports remain intact.

Managed capacity admission requires a recent, monotonically advancing provider storage measurement. It reserves 2 GiB per accepted query plus 1 GiB database headroom by default; these values require qualification against the approved query scope and provider. Older measurements cannot undo released reservations. Stale/unavailable samples stop new allocations while retained results remain readable. `python -m ops.serverless_control` provides status, capacity recording and an independently authenticated pause/resume path.

Managed PostgreSQL has a separate migration path with application-scoped role mapping, restricted runtime logins and explicit all-row read policies for backup. No API/worker ownership or BYPASSRLS grants are used. Login connection limits total 24 with eight further connections reserved in the proposed deployment budget. Provider-owned baselines are compared before and after migration. Managed imports use INSERT rather than COPY on RLS tables.

Separate operator commands stage private R2 objects within explicit byte limits, serialize operations with a PostgreSQL session lock and write sanitized durable reports. Backups retain one exported snapshot, object checksums, migration records, publication identities and hashes of all application tables including private rows. Restore requires an isolated empty application namespace and distinct object prefix. Restored dispatch starts paused.

Terraform versions the services, Jobs, service accounts, IAM, secret references, task queue, schedules and monitoring. Images and secret versions are immutable references; staging and production use separate state and namespaces. Public API access and unattended schedules default to disabled. See [deployment and recovery procedures](RESEARCH-SERVERLESS.md) and [cost worksheet](RESEARCH-SERVERLESS-COST.md).

## Local evidence

The [clean import report](../projects/research-workspace/verification/serverless-clean-import.json) records a full retained publication imported into an empty local PostgreSQL 17 database, exact source bundle hash, per-table/index sizes and fresh search growth. This is Windows/local allocated storage, not provider-billed usage or Cloud Run performance. It does not establish the final production publication scope.

Automated verification covers real PostgreSQL transactional rollback, missed wakeups, duplicate dispatch, uncertain launches, launch-rate and execution bounds, restricted dispatcher/worker roles, exact generation claims, cancellation ownership, stale-worker fencing, callback-free lease renewal, shared quotas, stale capacity, monotonic measurements and closed exports. Operator verification includes private-row visibility, isolated dump/restore hashes, lock contention/release and bounded object reads. Terraform validation uses the pinned provider schema and mock plans; it does not call a live Google project.

On the retained publication, the fresh general search completed in 97.171 seconds with 280,543 configurations and 211,961 champion sets, independently recounted in SQL. Allocated database growth was 716,046,336 bytes. The Jazz search completed in 11.160 seconds measured from database worker-start to finish, with 38,809 configurations and 30,935 sets; every configuration belonged to the general result. Final local allocated database size was 936,752,819 bytes. These timing scopes differ, and none predicts Cloud Run performance.

Integration commands (environment URLs point only to isolated local test databases):

```text
PYTHONPATH=.:data python -m pytest service ops data/test_import.py deploy/test_deploy.py solver/test_solver.py -q -rs
terraform fmt -check -recursive
terraform validate
terraform test -no-color
```

The final integrated Python run passed **131 tests and 27 subtests in 18.05 seconds**, with two fixture-dependent tests skipped because their expected source paths are absent in the review checkout. On Windows, `PYTHONPATH` used `.;data`; the equivalent Linux form appears above. The separate real-bundle import and full general/Jazz checks recorded above used the private development fixtures. Terraform schema validation and **six mocked plans** passed. New negative plans reject unreviewed spending, mutable images, unverified schedules, overlapping environment prefixes and staging-memory overcommit.

The [managed recovery report](../projects/research-workspace/verification/serverless-managed-recovery.json) records a real local PostgreSQL dump/restore with 75 matching application-table hashes, including an unpublished row, using a non-BYPASSRLS backup reader. Restore now fences copied cloud execution IDs before any drill dispatcher can inspect production work. Operator/import verification streams or uses explicitly bounded scratch storage. This does not substitute for the selected provider's R2 drill or Linux OOM tests.

An existing Windows concurrent-import test exposed inconsistent extended-path spelling during filesystem creation. Normalizing resolved Windows path prefixes preserves the containment boundary; its regression test and 150 concurrent immutable-write iterations passed. No source-object overwrite policy was relaxed.

## Remaining work before cloud rehearsal or launch

- The owner has set a total monthly ceiling below US$5 (configuration maximum US$4.99) and authorized setup within it. The current five-minute database-polling design is over budget; see the revised cost worksheet. Complete owner sign-in, project/billing setup, an experiment suballocation within the ceiling, hostname, alerts and publication scope. No provider has been selected from local evidence.
- Install and verify the selected provider's recurring capacity collector. Manual samples expire after 15 minutes. The default reservation is an admission estimate, not proof of a hard storage ceiling for every million-result query.
- Resolve the irreducible ambiguous-launch case with operator evidence. If a process dies before sending `jobs.run`, the persisted intent is indistinguishable locally from a lost acknowledgement. Discovery can recover an actual execution, but an empty eventually consistent listing cannot prove no execution exists. The implementation holds that reservation; it does not yet guarantee the PRD's 15-minute recovery bound for this case.
- Qualify certificate verification, direct/pooled transactions, lock semantics, connection exhaustion, provider permissions, billed storage including branches/history and cross-provider latency. Repeat a real R2 restore using that provider; the local test is not a managed-provider drill.
- Verify native cold compilation lease renewal and the 120-second hosted exhaustive-search target; separate startup, compilation, enumeration, persistence and browser completion. A simulated callback-free delay proves the heartbeat mechanism, not Cloud Run/Numba scheduling.
- Run the normal/failure dispatch timing tests, hosted 50-session load, tenfold growth, browser/accessibility checks, authenticated ingress attacks, operator termination/OOM tests and a delivered failure notification.
- Observe at least 24 hours with the proposed schedules, reconcile actual billing usage, then perform backup/cutover/rollback and a computer-off browser check. No SL-01â€“SL-12 criterion is certified complete by this local work.

The backend is mirrored to the development `research-app/` directory without replacing `.local` data. Credentials, restored objects, dumps and Terraform state remain outside Git.
