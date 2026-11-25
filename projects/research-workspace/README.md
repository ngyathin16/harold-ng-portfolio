# Research workspace

For repeatable Linux setup and private recovery, use the [deployment runbook](deploy/README.md) and [R2 backup runbook](ops/backup-README.md). The [capacity-wait verification record](../../docs/RESEARCH-CAPACITY-IMPLEMENTATION.md) separates observed local results from remaining launch checks. Research file downloads are no longer public.

This application adds a browser explorer and asynchronous team queries to the existing TFT research snapshots. The Astro interface lives in the portfolio's `/applications/` and `/applications/research/` routes. This directory contains the independent Postgres data service, publication importer, rules, solver and worker. The collector is maintained separately; pass its `tft-set-19` directory with `prepare --project` when importing from this checkout.

The implementation has been exercised against a local Postgres instance with the real source snapshot. Production database, API, worker and the final HTTPS application still require provisioning and hosted verification. Oracle API and private R2 access passed checks on November 23, but Oracle currently reports no Always Free A1 capacity in the Singapore home region. The owner wants free or minimal cost and does not require Supabase; see the [hosting decision](../../docs/RESEARCH-HOSTING.md). No paid spending ceiling is agreed. Local success does not establish the PRD's hosted performance, availability or cost targets.

## Local setup

Use Python 3.12 or newer and Postgres 15 or newer. From this directory:

```powershell
python -m pip install -r service/requirements.txt numpy numba
```

Copy `.env.example` to a local ignored `.env` for a deployment tool or container runner that loads environment files. The Python commands read **process environment variables**; they do not implicitly load `.env`. Keep the collector's Riot key in its existing separate local file. Browsing, import and team queries do not need a Riot key.

Create the database and `anon`, `authenticated` and `service_role` roles for plain local Postgres. Supabase supplies these roles. Apply the versioned SQL in this order using the privileged import connection:

1. `migrations/001_publications.sql`
2. `migrations/002_source_tables.sql`
3. `migrations/003_source_views.sql`
4. `service/migrations/002_queries.sql`
5. `migrations/004_storage.sql` on Supabase
6. `migrations/005_service_roles.sql`
7. `migrations/006_private_artifacts.sql`

For example, `psql --dbname $env:DATABASE_URL --set ON_ERROR_STOP=1 --file migrations/001_publications.sql` applies the first file. Migrations are applied once, in order. They create normalized source tables with scoped identities, translated research views, publication policies and application job tables. Do not expose these schemas with write grants to visitor roles.

Prepare and import the real source snapshot. These commands resolve `latest` once and produce a frozen bundle:

```powershell
python data/import_snapshot.py prepare --project 'C:/Users/Harold NG/Documents/research/tft-set-19' --output .local/bundle
python data/import_snapshot.py publish .local/bundle --local-storage .local/objects
```

`DATABASE_URL` is required for the second command. The filesystem store supports private local fixtures and a proposed single-VM deployment using a persistent volume. Restrict it to operator recovery and cleanup, and back it up independently. It is not a public static directory. For Supabase, omit `--local-storage` and supply `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. See [the publication guide](data/README.md) for source reuse review, artifact validation, scoped derivatives, replay and schema compatibility.

Start the API and worker in separate terminals with their environment variables set:

```powershell
python -m uvicorn service.app:app --host 127.0.0.1 --port 8788 --no-access-log
python -m service.worker
```

Set `RESEARCH_DATABASE_URL` for both, `RESEARCH_SESSION_SECRET` to a strong random server secret for the API, and `RESEARCH_ARTIFACT_ROOT` to the absolute `.local/objects` path for private object cleanup and recovery. The worker leases durable Postgres jobs independently of HTTP requests. Run one or more worker processes; the configured concurrency cap limits simultaneous execution. Queue admission, per-session and network request limits, deadlines, result caps and cache retention are configurable in `.env.example`.

In the portfolio checkout, set `PUBLIC_RESEARCH_API_URL=http://127.0.0.1:8788` and use its existing `npm run dev` command. Open `/applications/research/`. Only that public API base URL belongs in the portfolio environment; the site builds without this research checkout, SQLite files or database credentials. An unconfigured service produces an unavailable state with retry, rather than an empty dataset.

## Data and query behavior

Source references retain dataset, snapshot, publication and stable record IDs. Publication changes are independent of website builds. The importer uploads and checks private immutable objects, reads back every Postgres table and view to compare logical rows with SQLite, then publishes and advances the dataset pointer in one transaction. Unpublished data stays inaccessible through tables, views and storage. Repeating an import reuses the same verified publication. Earlier publications and their rules remain available for saved links.

Catalog search, filters, table sorting and pagination execute in Postgres. Visitors receive bounded pages rather than the whole source database. Source JSON, nulls, zero, empty text, variants, uncertain lifecycle values, archived evidence and unresolved conditions retain their original meanings. Source bundles, manifests, reports, and compressed SQLite files remain private operator recovery artifacts. The public API and interface provide no research downloads.

Team definitions pin the complete filter set, publication, ruleset and solver version. The API coalesces identical work and issues a separate opaque subscription capability to each session. Public links grant read access, not cancellation authority. Cancelling one subscription leaves other visitors' shared work active. Only an exhaustively completed search can produce authoritative result counts and global sorting. Failure, cancellation, a deadline or a result cap remains incomplete.

The rules describe hypothetical final boards under recorded assumptions. They preserve explicit forms and champion groups, use board slots separately from champion counts, and distinguish proven active, inactive and unknown traits. Jazz uses the same breakpoint evaluator as other traits. See [the solver guide](solver/README.md) for assumptions, independent verification and CLI usage.

The durable queue currently uses application-owned Postgres job rows and `FOR UPDATE SKIP LOCKED` leases. This is an explicit implementation choice in place of the PRD's default Supabase Queues extension. The worker still executes in a separate container and persists state across delivery and restart. Measure and review the queue against hosted admission and recovery targets before release.

## Verification

Run local tests and publication recovery checks:

```powershell
python -m unittest discover -s data -p test_import.py -v
python -m unittest solver.test_solver -v
python -m pytest service -q
python data/verify_postgres.py .local/publication-verification.json
```

The Postgres verifier uses `DATABASE_URL` and `RESEARCH_SOURCE_PROJECT` (the collector directory), creates isolated fixture datasets, and removes only those fixtures afterwards. Set `RESEARCH_TEST_DATABASE_URL` for the service's real database tests; otherwise those checks are explicitly skipped. The verifier publishes two different snapshots with overlapping local identities, checks typed table and view parity, repeats imports, rejects corrupted parity, verifies rollback and tests anonymous access to drafts. The fixture adapter advertises ordinary browsing without TFT team-query capability.

Historical local data checks on November 20, 2026, before public download removal:

- The real source snapshot imported **58 tables and 17 views**, containing **156,868 source table rows**, with logical parity for every relation.
- **123 downloads** passed their declared SHA-256 checks through the application API; decompression restored the exact SQLite hash. Those source artifacts total **89,671,406 bytes per publication**. This is artifact payload size, not a forecast of Postgres disk, egress or cost.
- Repeated imports, two-version isolation, failed publication recovery and anonymous table/view access checks passed.
- A dedicated local growth database passed parity and count checks at **1,568,680 source table rows across ten scopes**. Query plans are recorded; this is not a hosted concurrency result.
- A local `pg_dump`/`pg_restore` rehearsal into a separate database verified two publications, 246 separately copied objects and three saved query definitions. The restored catalog and saved queries reopened through the API. Hosted backup facilities and recovery-time targets remain unverified.

Local verification reports are generated under `.local/`. The complete PRD acceptance set also requires real hosted journeys, keyboard and responsive checks, final HTTPS routes, ten-minute concurrent workloads, growth fixtures, queue/runtime measurements, backups and cost review. Record those results on the chosen resources before describing the product as released.

## Deployment and operation

Build the API/worker image from this directory with `docker build -f service/Dockerfile -t research-service .`. Deploy it as two independent services: the image's default API command, and a worker command `python -m service.worker`. Keep both near the managed database. Use bounded database pools and statement timeouts; scale worker concurrency independently of catalog reads. Configure HTTPS and the allowed portfolio origin. Restrict trusted proxy headers to the actual ingress so network quotas cannot be bypassed with arbitrary forwarded headers.

Use separate server secrets and roles for imports, API reads/job admission, and worker result writes. Migration 005 creates `research_api` and `research_worker` groups without login privileges. Grant each group to its own server login role. Both inherit published source reads; neither can write source facts or publication state. Only the worker can insert computed results. Keep privileged import credentials separate. Never place `DATABASE_URL`, `RESEARCH_DATABASE_URL`, Supabase secret keys or the session secret in Astro's `PUBLIC_` variables. Rotate server secrets through the host's secret store. The session secret's rotation invalidates existing management capabilities; public query links remain readable.

Before public data publication, review the source registry's attribution and reuse conditions. The full-publication adapter includes all source tables; exclusions need a verified derivative with a distinct publication identity and matching database, evidence, roster and private recovery scope. Source captures marked for local research must not be silently described as cleared for unrestricted redistribution.

Production release gates:

- Select Supabase and worker ownership, region, plan, monthly budget and alert thresholds. No paid plan is selected by this implementation.
- Apply migrations, private bucket policies and least-privilege credentials; test anonymous direct requests and rejection of retired download URLs against the hosted project.
- Import a named snapshot and repeat the import and failure tests. Record managed table/index bytes, object bytes and backup sizes separately.
- Deploy the API and worker; verify the full Jhin preset with and without Jazz on uncached queries. Preserve exact versions, independent result recounts, counts and bounded result browsing.
- Run the PRD's 50-browser/two-worker workload and tenfold data-growth fixtures. Record latency percentiles, errors, queue wait, memory, connections, compute, disk and egress. Set budgets from those measurements.
- Set the portfolio's public API URL, run its existing release checks, deploy through the verified workflow, and test navigation, deep links, old pages, sitemap, redirects, 404, research download removal, unrelated portfolio downloads, keyboard use, narrow layout and 200% zoom over HTTPS.

Monitor ordinary-read latency, errors, active database connections, queue age, worker attempts, query duration, result growth, object storage and egress. Run `python -m service.worker --cleanup` on an operator schedule to remove expired result rows and old quota counters. Keep public query definitions, retained source publications and frozen rulesets; recomputation must use the original rules. Cleanup is explicit and does not run on visitor page load.

Back up Postgres and object storage **separately**. Retain source SQLite bundles and their hashes outside both services. Test restoration into an isolated database and private bucket, replay a named snapshot, compare its logical rows and objects, and reopen a saved query definition. A Postgres backup does not restore storage objects. Record recovery time and tolerated data-loss windows with the selected hosting plan.

For a website rollback, redeploy the previous portfolio release while preserving the API contract and immutable publications. For an API rollback, deploy the previous compatible service image; keep source data intact. A changed source schema requires a reviewed additive migration and a newly verified publication. Never overwrite existing snapshots or run a destructive reverse migration to roll back the interface.
## Serverless deployment

The Cloud Run migration package is in `deploy/serverless/`. Read the [implementation evidence](../../docs/RESEARCH-SERVERLESS-IMPLEMENTATION.md), [runbook](../../docs/RESEARCH-SERVERLESS.md), and [cost worksheet](../../docs/RESEARCH-SERVERLESS-COST.md) before provisioning. Local behavior is implemented; managed-provider qualification and hosted acceptance are pending. The existing VM deployment remains available.
