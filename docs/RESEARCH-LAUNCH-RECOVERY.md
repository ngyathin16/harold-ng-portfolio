# Recovering interrupted search launches

The dispatcher persists a conditional Cloud Run Job update before sending it. Recovery reuses that reservation and its execution token, preserving the global limit of two outstanding executions and three paid attempts per query. Local failure tests pass. Actual execution naming, conditional replay, dispatcher permissions and hosted timing remain deployment gates.

The earlier protocol committed an execution reservation before calling `jobs.run`. If the dispatcher stopped between those steps, the reservation could remain uncertain indefinitely. Repeating the request after a lost response could instead create a second paid execution. Neither an expired SQL timestamp nor an empty execution listing proves that the original request cannot still run.

## API contract

`jobs.run` has no caller-supplied request identifier or execution name, and the Execution API has no create method. These interfaces cannot directly provide the required replay identity. [Run request](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/run), [Execution methods](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions)

The Job resource supplies `startExecutionToken`, a unique execution-name suffix, and `etag` for modification conflicts. Updating a Job can request an immediate execution. The implementation combines those mechanisms; it does not treat an etag on `jobs.run` as a deduplication token. [Job fields](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs), [Immediate execution](https://docs.cloud.google.com/run/docs/execute/jobs), [Google etag guidance](https://google.aip.dev/154)

The inference to qualify on Cloud Run is that one conditional update creates at most the named execution, while a stale conditional update cannot overwrite later desired state or create another execution. Public documentation does not explicitly establish token replay retention or execution-name tombstone lifetime. Old requests therefore retain their conditional version; recovery never supplies an arbitrary fresh etag or recreates the Job.

## Durable protocol

Migration 012 adds the protocol, persisted request and cloud-confirmation time to each reservation. Existing `jobs.run` reservations remain observation-only; new CloudRun adapters have no fallback to that launch method.

1. Reserve the paid attempt and outstanding slot in PostgreSQL. The reservation's random 32-character hexadecimal identity becomes the execution suffix.
2. Read the allowlisted Job. Verify its digest-pinned image, bound worker entrypoint, one task/container, zero platform retries, 300-second timeout and 2 CPU/2 GiB allocation. Persist the Job UID, generation, predecessor token, configuration fingerprint and conditional request before sending it.
3. Preserve the reviewed configuration while setting the three worker binding values and token. UTF-8 request bytes are limited to 60,000. Each control attempt debits its separate call allowance and 131,072-byte egress reserve first. A transport replay does not reserve another paid search attempt.
4. Resolve the deterministic execution and check all three binding values. Only that observation confirms the launch; only its completion permits another attempt. Missing responses, 404s, permission errors and expired reservations retain capacity.
5. Replay only while the predecessor UID, desired generation, token and configuration still match. A status-only etag change is persisted before replay. A changed deployment remains held for operator review. When the desired token is already present, wait for its execution to appear.

Only one unconfirmed Job mutation is allowed globally. After its execution is observed, a second search may start while the first runs. The SQL index and admission check enforce this ordering. Deployments must pause and drain or fence dispatch before changing the shared Job.

An unconfirmed launch returns retryable HTTP 503 to the authenticated wakeup. Normal active work returns HTTP 200. The finite Task policy allows five deliveries with 5/10/20/40-second backoffs; application retries create no additional Tasks. Two accepted queries reserve at most ten scheduled Task deliveries, subject to the documented possibility of duplicate platform delivery. [Task retry configuration](https://docs.cloud.google.com/tasks/docs/configuring-queues#set_retry_parameters), [Delivery limitations](https://docs.cloud.google.com/tasks/docs/common-pitfalls)

The dispatcher uses five-second connection/statement timeouts and a three-second lock timeout. A 150-second process watchdog terminates stalled request work with exit code 124. Closing the process rolls back uncommitted SQL; committed launch requests remain recoverable. This bounds ordinary application work without asserting a hard invoice limit under platform duplication or process scheduling failures.

## Evidence and remaining checks

The full local service run passed 346 tests in 40.07 seconds, with one Windows file-symlink-privilege skip. It included 17 token tests covering both crash windows, concurrent reconcilers, delayed stale updates, two outstanding/six total paid executions across two searches, exact worker binding and UTF-8/resource limits. Four timeout tests exercised a real blocked SQL lock and watchdog termination/cancellation in real subprocesses. Provider behavior was mocked. Six managed installation and actual backup/restore tests passed on a fresh PostgreSQL 17 cluster using SCRAM authentication and a non-superuser migration owner, including worker column permissions before and after restore. [Sanitized local report](../projects/research-workspace/verification/serverless-launch-token-tests.json)

On November 26, 2026 at 01:00:26 UTC, an owner-authenticated provider check performed GET, `PATCH?validateOnly=true`, then GET. Cloud Run accepted the 2,663-byte request with HTTP 200; Job identity, generation, configuration, execution count and token remained unchanged. This proves request-shape acceptance with the owner credential. It created no execution and proves neither dispatcher authorization nor replay behavior. [Provider validation report](../projects/research-workspace/verification/serverless-launch-token-validation.json)

Before release, use the first real reserved search to verify the suffix-derived name, stale-etag rejection before side effects, preserved configuration and one paid execution after replay. Then qualify a second concurrent search and measure the PRD's 60-second normal-start and 15-minute recovery bounds. The live managed installation must preserve the worker's limited `state`/`claimed_at` update grants while denying changes to launch intent.
