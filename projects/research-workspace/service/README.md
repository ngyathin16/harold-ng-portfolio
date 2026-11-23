# Research API and worker

The HTTP API reads published Postgres snapshots and admits bounded team searches. A separate worker leases jobs from Postgres and writes idempotent result batches. Neither process collects sources. The portfolio needs only the public API URL.

From `research-app/`:

```powershell
python -m pip install -r service/requirements.txt
python -m uvicorn service.app:app --host 127.0.0.1 --port 8788
python -m service.worker
```

Set environment variables from [`.env.example`](.env.example) in the process or hosting secret store. The service does not load `.env` automatically. `RESEARCH_DATABASE_URL` must be a Postgres connection string. Use the provider's pooler appropriate for a persistent container. `RESEARCH_SESSION_SECRET` is an independently generated random secret, shared by API replicas; rotating it revokes existing cancellation capabilities. Keep database, storage, and session secrets off the browser. `RESEARCH_ALLOWED_ORIGINS` is an exact comma-separated list, including each development origin and port where needed. Set `RESEARCH_ARTIFACT_ROOT` only for local development with the importer's filesystem object store.

Apply the source data migrations, then `service/migrations/002_queries.sql`, `migrations/005_service_roles.sql`, and `migrations/006_private_artifacts.sql`. Provision separate login roles inheriting `research_api` and `research_worker`, respectively. These groups cannot publish or change source facts. The importer uses a separate privileged credential. Do not use the owner connection used for local integration tests in production. Browser users have neither credential; all public calls go through this API. Anonymous and authenticated Supabase roles have no access to queue internals or write authority over results.

Build the container from `research-app/`:

```powershell
docker build -f service/Dockerfile -t research-service .
docker run --env-file service/.env -p 8080:8080 research-service
docker run --env-file service/worker.env research-service python -m service.worker
```

The API and worker can scale independently. Worker admission is enforced in Postgres across replicas. Each worker process runs one job; the database enforces the configured shared concurrency ceiling. The default queue holds at most 100 active jobs, with two running, three active submissions per session, twelve submissions per session per minute, and thirty per client network address per minute. Ordinary reads have a separate 600-request network limit per minute. Configure trusted proxy forwarding in the hosting layer; do not trust arbitrary client `X-Forwarded-For`. Place a 16 KiB request-body cap and connection/rate limits at the public proxy as defense against oversized traffic before Python parsing.

The queue is implemented with Postgres rows, `FOR UPDATE SKIP LOCKED`, leases, and transaction advisory locks. It needs no Supabase-specific queue extension. A lease owner must still hold its lease before every result batch and final completion; an old worker cannot overwrite its replacement. Crash recovery restarts a query from its frozen definition, clearing the previous attempt's provisional results, with at most three attempts. Results have unique `(run_id, configuration_key)` identities. Complete counts are independently compared with stored rows. Runtime or result limits produce `resource_limited`, never `complete`.

Equivalent normalized queries coalesce only when dataset, publication, snapshot, rules, filters, and solver version agree. Each visitor gets a separate subscription. Store returned `session_token` locally and send it as `X-Session-Token`; never put it in a share URL. Cancelling requires that token and its matching subscription ID. Cancellation stops the shared run only after all active subscriptions cancel. Retrying a terminal run closes the previous generation's subscriptions before attaching the new visitor. Public query IDs grant read access only.

Run `python -m service.worker --cleanup` daily to remove expired result rows, stored query exports and old quota counters. Configure the same storage settings as the worker. Old artifact keys are retained in a garbage-collection table until deletion succeeds; retrying a terminal run schedules its superseded objects for deletion after the retained cleanup delay. Definitions and frozen rules remain, and submitting the same definition recomputes an expired result. Results default to 30 days retention. Partial cancelled and limited runs are also retained for this period. Deploy the historical solver version if a saved query refers to a different version; the worker refuses to substitute one. Back up Postgres and the private object bucket separately.

Public database, table, report, and team-result downloads are retired. Their former listing, artifact, and query export URLs return 404 for GET and HEAD. The API never signs storage URLs or streams complete exports. Query responses contain bounded results and research provenance, without export metadata. The worker completes searches without artifact storage and does not automatically prepare exports. Private operator export helpers and retained objects remain available for recovery.

Apply `migrations/006_private_artifacts.sql` after migration 005, including existing installations. This removes the legacy anonymous artifact grant and Supabase object-read policy and retires the dataset download capability. Keep the object bucket private. Preserve source bundles and registered recovery objects; removing public delivery does not authorize deleting them.

Set `RESEARCH_CAPACITY_PATH` to a readable directory on the database filesystem and `RESEARCH_MIN_FREE_BYTES` to the required free-space reserve. New query allocation and worker claims stop below this threshold or if the path is unavailable. Existing reusable jobs remain readable. A running worker checks before each result batch and marks its run resource-limited if capacity is unavailable. This reserve is an admission guard, not an exact disk quota; concurrent filesystem users can still consume space. Configure monitoring and enough headroom for database writes and backups.

Legacy object cleanup holds the `research-backup-objects` advisory lock through selection, deletion, and unregistering. The private backup command holds that same lock while copying the objects referenced by its database snapshot, so cleanup cannot remove them midway through staging.

## REST contract

OpenAPI is available at `/docs`. All scoped reads use an immutable publication ID; latest is resolved through the dataset index. Pagination starts at one and is capped at 200 rows.

| Endpoint | Response |
| --- | --- |
| `GET /v1/datasets` | `{datasets:[{id,name,description,capabilities,latest_publication_id,latest_publication}]}` |
| `GET /v1/datasets/{id}/publications` | `{publications:[...]}` |
| `GET /v1/publications/{id}` | Published manifest, scope, snapshot and timestamps |
| `GET /v1/publications/{id}/catalog` | `{rows,total,page,page_size,kinds}`; search, category, cost, trait, shop, locked, item and rarity filters |
| `GET /v1/publications/{id}/records/{record_id}` | `{record,sections,evidence,evidence_total,source,gaps,gaps_total,relationships}` |
| `GET /v1/publications/{id}/tables` | `{tables:[{name,columns,primary_key,row_count,...}]}` |
| `GET /v1/publications/{id}/tables/{name}` | `{rows,total,page,page_size,columns,truncated}`; typed filters run before pagination |
| `GET /v1/publications/{id}/coverage` | Coverage, source health, discrepancy samples, full issue counts, source validation |
| `GET /v1/publications/{id}/rules?version=...` | The frozen ruleset; absent historical versions return 404 |
| `POST /v1/queries` | Body `{publication_id,query}`; returns `{query_id,state,subscription_id,session_token,query}` |
| `GET /v1/queries/{id}` | Public definition, versions, state, progress, completeness and counts |
| `GET /v1/queries/{id}/results` | Paginated results; final sorting only when complete |
| `POST /v1/queries/{id}/cancel` | Body `{subscription_id}` and `X-Session-Token` header |

Table `filters` is a JSON array such as `[{'column':'cost','op':'gte','value':3}]` encoded with valid JSON double quotes. Allowed operations are `eq`, `gte`, `lte`, `contains`, and `is_null`. Table and column identifiers must exist in the publication's imported registry; values are bound parameters. Large text values are previewed at 4,096 characters and identified in `truncated`; request `full=true&page_size=1` with the same filters and sort to expand one row. Views use all original columns as stable tie breakers, and tables use their original composite keys. Source values remain unchanged in full-field reads.

## Verification and launch limits

```powershell
python -m pytest service/test_service.py -q
$env:RESEARCH_TEST_DATABASE_URL='postgresql://...'
python -m pytest service/test_service.py -q
```

Postgres tests add isolated fixture publications and remove their own rows. They cover equivalent-job reuse, cancellation ownership, lease expiry and fencing, restart, retired public downloads, storage-independent completion and capacity admission, cache expiry, publication visibility, and contradictory queries. Tests without a configured database cover unavailable-state behavior and SQL filter boundaries; integration tests are explicitly skipped.

Local verification does not establish hosted latency, capacity, a monthly budget, or production deployment. Measure two uncached real searches while fifty browsing sessions run, cold response transfer, database and object sizes, worker RSS, queue age, and p95 latency on the chosen host. Set alerts on queue age, failed/limited jobs, database connections/disk, artifact egress, and worker restarts. `/health` checks database connectivity without exposing configuration.

Run the HTTP workload with `python -m service.load_check --api https://API_HOST --queries verification/two-queries.json --output verification/hosted-load.json`. The input is an array of exactly two valid POST submission bodies for uncached queries. Start two independent workers first. Defaults are fifty read sessions, six-second read intervals, and ten minutes. The harness records latency, errors, queue wait and completion, rejects completed-cache reuse, and exits unsuccessfully when targets fail. It does not measure host memory, CPU, disk or egress; collect those separately from the provider. Use a fresh isolated publication for an uncached repeat, preserving earlier query identities.

The historical [local service measurements](../verification/service-local.json) cover real Postgres writes and complete streamed results, with explicit machine context. They describe the earlier export-enabled implementation, not the current public API or the ten-minute hosted concurrency test. In this run, the two persisted result sets occupied about 813 MB including indexes, and the uncompressed general JSON export was about 1.20 GB. Retention, storage plan, worker CPU and egress must therefore be budgeted separately before launch.

Implementation references: [Psycopg connection pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/).
