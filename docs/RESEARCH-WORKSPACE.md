# Research workspace integration

The [capacity-wait PRD](RESEARCH-CAPACITY-WAIT-PRD.md) supersedes public-download requirements below. Deployment and private recovery tooling are implemented, public research download routes and controls are removed, and the local R2/browser/statistics checks pass. See the [implementation and verification record](RESEARCH-CAPACITY-IMPLEMENTATION.md) for evidence and the remaining Linux/hosted gates. Historical download results below describe the earlier implementation.

The application uses `/applications/` and `/applications/research/`. It preserves the current portfolio layout and navigation, including the existing component attribution in `COMPONENT-ADOPTION.md`. Research JavaScript is loaded only on the workspace route. The static build does not require a research folder, source database, or secret credentials.

The backend source is in `projects/research-workspace/`. Its importer, Postgres migrations, private storage policies, API, durable queue and compute worker deploy independently of the portfolio. Source collection remains a separate operation. Read the backend README before configuring a hosted service.

## Local operation

Run the backend against a migrated Postgres database, import a verified snapshot, and start its API and worker. The backend README describes the required environment and commands. Supply the source project explicitly when preparing an import from this checkout:

```powershell
python projects/research-workspace/data/import_snapshot.py prepare --project 'C:/Users/Harold NG/Documents/research/tft-set-19' --output 'projects/research-workspace/.local/bundle'
$env:PUBLIC_RESEARCH_API_URL='http://127.0.0.1:8788'
npm run dev
```

Configure `RESEARCH_ALLOWED_ORIGINS` on the API to include the exact local Astro origin and port. Open `/applications/research/`. The only public configuration is the API base URL. Never prefix database or storage secrets with `PUBLIC_`.

With an imported dataset and API available, `BASE_URL=<local portfolio origin> npm run test:research` exercises search, record evidence, retained version URLs, table pagination beyond 200 rows, downloads, generic trait requirements, contradictions, mobile layout and network failure. Set `TEST_COMPLETED_QUERY=1` to include submission and persisted complete results; a worker must be running or the exact completed query must already exist. This browser check does not measure uncached solver performance.

## Release state

Implementation and local verification are available. Oracle and private R2 access were verified on November 23. Production deployment is currently blocked on Always Free A1 compute capacity in the Oracle home region, Singapore; service deployment and source publication scope remain pending. The owner requested free or minimal cost; Supabase is optional. See the [free-first hosting decision](RESEARCH-HOSTING.md) for the access results, single-VM setup and operational tradeoffs. No paid resources have been provisioned. The application must not be described as publicly hosted until the final HTTPS journeys, hosted policies, workload and recovery checks pass.

Verified locally on November 20, 2026:

- Real SQLite import matched all 58 source tables and 17 research views in Postgres; 123 artifacts and the restored SQLite hash passed verification.
- Import retries, failed publication rollback, second-dataset fixtures, two-version isolation and anonymous restrictions passed. Dedicated API and worker roles could not change source facts.
- A fresh Postgres database passed the packaged migration sequence and 33 backend tests, including actual API/worker/anonymous permission checks. Two source-data tests skip in a clean checkout; their real-data checks passed in the research workspace.
- Solver fixtures matched an independent brute-force implementation. General and Jazz-constrained Jhin queries completed, every emitted team was independently recounted, and the Jazz configurations were a subset of the general query.
- Browser journeys passed at 1280 and 390 pixels, with enlarged text, keyboard access and automated WCAG checks. The existing portfolio suite passed 24 page/viewport checks, 20 design combinations and navigation checks.
- A clean static build passed type checks, release validation and the existing 90 KB gzip JavaScript budget. The entire site's conservative first-party JavaScript total was below that budget, including the new application.

Local diagnostics are not hosted service-level measurements. Exact solver versions, frozen rules, counts and limitations are recorded under the backend's `solver/verification/` and `verification/`. Production still needs the ten-minute concurrent workload, tenfold growth checks, storage signing, backup restoration, cost review and final deployment checks from the PRD.

## Versioning and rollout

`latest` is resolved once to a concrete publication and snapshot in the URL. Every request and stored query uses that scope. Checking for updates reads the publication index; it never runs the collector. Opening a newer version requires an explicit choice. Null, false, zero, empty text, archived evidence and unresolved activation retain distinct presentations.

The source text is rendered as DOM text; it is never inserted as HTML. Private job-management capabilities stay in browser storage and do not appear in copied query URLs. API authorization is also enforced server-side. Complete result exports require exhaustive completion; partial runs are labelled incomplete.

Add the production public API URL to the Cloudflare build environment only after the backend is verified. Publish through the existing reviewed GitHub workflow. Updating research data then requires only an importer run, not a website rebuild. Roll back the interface or compatible API image independently; retain immutable publications and their frozen rules.

Implementation references: [Astro browser scripts](https://docs.astro.build/en/guides/client-side-scripts/), [Supabase row policies](https://supabase.com/docs/guides/database/postgres/row-level-security), [private storage policies](https://supabase.com/docs/guides/storage/security/access-control).
