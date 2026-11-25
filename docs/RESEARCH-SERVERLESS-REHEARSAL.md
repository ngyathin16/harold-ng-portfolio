# Bounded private rehearsal

Reviewed November 25, 2026. The owner authorized setup under a US$4.99 total monthly ceiling. This record authorizes one private staging rehearsal within that existing scope; it does not certify production or unlimited public use.

Reserve US$1.72 for a full 31-day month of fixed Google resources and US$3.00 for finite experiment usage, totaling **US$4.72**. Keep US$0.27 unallocated. Conservative list-price arithmetic for the specified work totals approximately US$3.95, before shared Google free credits. Recheck account currency and observed usage before each dependent stage. Stop if any bound cannot be measured or enforced. Do not enable a paid Neon plan.

| Item | Maximum covered | USD estimate |
| --- | --- | ---: |
| Secret Manager | 18 active automatic-replication versions, 31 days | 1.11 |
| Artifact Registry | Two image digests, aggregate at most 2 GiB, 31 days | 0.21 |
| Scheduler | Four schedules, including paused schedules, 31 days | 0.40 |
| Cloud Run services | 10,000 vCPU-seconds and 5,000 GiB-seconds, startup/shutdown included | 0.3535 |
| Operator Jobs | Six executions, each at most 1,800 seconds at 1 CPU/4 GiB | 0.33696 |
| Search Jobs | Six executions, each at most 300 seconds at 2 CPU/2 GiB | 0.0864 |
| Job overhead reserve | Twelve executions, 240 additional seconds each at 2 CPU/4 GiB | 0.152064 |
| HTTP requests | 500 service requests total | 0.0002 |
| Google internet output | 5 GiB across all services, conservatively US$0.23/GiB | 1.15 |
| Secret access | 10,000 operations | 0.03 |
| Tasks | 10,000 billable operation units | 0.004 |
| GCS hints | 10,000 Class A and 10,000 Class B operations; at most 1 MiB stored | 0.05402 |
| Monitoring API | 100,000 returned time series | 0.05 |
| Logging/alerts | At most 1 GiB logs; existing four policies; no paid custom integrations | 0 |
| Neon | Free project only | 0 |
| R2 | Within owner-confirmed free allowance and bounded additions below | 0 |
| GitHub | Existing public repository, standard hosted Linux runner, small one-day report only | 0 |

Sources: [Cloud Run Singapore pricing](https://cloud.google.com/run/pricing), [network](https://cloud.google.com/vpc/network-pricing), [Secret Manager](https://cloud.google.com/secret-manager/pricing), [Artifact Registry](https://cloud.google.com/artifact-registry/pricing), [Scheduler](https://cloud.google.com/scheduler/pricing), [Tasks](https://cloud.google.com/tasks/pricing), [GCS](https://cloud.google.com/storage/pricing), [Observability](https://cloud.google.com/products/observability/pricing), [Neon](https://neon.com/docs/introduction/plans), [R2](https://developers.cloudflare.com/r2/pricing/), [GitHub](https://docs.github.com/en/billing/concepts/product-billing/github-actions). Alert-policy pricing is currently deferred until no sooner than September 1, 2027; recheck before a later rollout.

## Execution order and limits

1. Verify the dedicated project/account, existing HK$15 account warning, HKD currency, and owner notification destination. Link only this project. Configure and read back an **HK$8 Cloud Run spend cap** before deploying runtime services. `python -m ops.cloud_spend_cap --config PRIVATE_JSON` plans; explicit `--apply` creates an absent cap. It never changes an existing cap. A configured cap with unfinished reconciliation is not ready. HK$8 is approximately US$1.04 at the conservative HKMA band edge; this is not Google's contractual exchange quote. [HKMA](https://www.hkma.gov.hk/eng/key-functions/money/linked-exchange-rate-system/)
2. Bootstrap only the registry and tightly scoped GitHub OIDC build identity. Build the two images locally on the runner, reject a combined uncompressed size above 2 GiB, and check existing registry storage before pushing. Keep at most two image digests. Record the exact clean source commit and digests before normal Terraform planning. No Cloud Build, vulnerability scanning, paid runners or large image artifacts.
3. Use one staging environment, owner-only invocation, zero minimum instances and disabled operator schedules. Search acceptance stays paused until measured provider capacity supports it. Never lower the application's reservation/headroom merely to fit Free. Keep startup CPU boost disabled or count boosted startup in the CPU ledger. The timing reserve above is an execution limit to check, not a platform guarantee.
4. Before invoking any service or Job, record its maximum contribution in the private experiment ledger. Count retry attempts and failures. Stop on an unexpected loop, uncertain execution, transfer excess or missing measurement. Platform request deadlines alone do not stop all processing. At most two admitted queries and six resulting search executions are included; do not run them if storage qualification fails.
5. At most six operator executions are included: import, backup, verify, restore, prune and cleanup. Use immutable source objects and separate staging recovery namespaces. Existing backup sets remain untouched. All Google internet output shares the 5 GiB bound, including object uploads, database writes, downloaded results and registry traffic.
6. Enable the five-minute reconciler only after a deadline mechanism and resource-time ledger are verified. A single 24-hour window allows at most 288 scheduled calls, included in the 500-request total. Pause at the deadline or earlier on any exhausted bound. Without that mechanism, keep the schedule paused and use finite manual invocations.
7. Verify actual usage and account caps after each stage. Fixed costs remain reserved even if work stops. Keep public access disabled and do not claim computer-off production readiness from this rehearsal.

The Cloud Run cap covers one service category in one project, uses gross costs, can overshoot due to enforcement delay and resets next month. It excludes other products and cannot guarantee the whole US$4.99 ceiling. The finite workload bounds and remaining margin are still required. The pinned Terraform provider does not expose the cap field, so its alert-only budget remains separate from the operator REST command. [Spend-cap limitations](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)

## R2 allowance evidence

The owner reported current account usage of **295.35 MB, 271 Class A operations and 785 Class B operations** on November 25. One private bucket listing found 252 Standard objects totaling 295,347,042 bytes, matching the rounded account total. No analytics token is available; account operation totals are owner-provided, not independently API-verified.

Allow at most 5 GiB additional retained objects and 10,000 additional Class A/Class B operations each. Total retained bytes would be at most 5,664,056,162, below the current 10 GB allowance. Existing totals plus these operation limits remain below the included allowances. Recheck if other account workloads change. Paid Class A units round upward to millions; exhausting the free allowance can add US$4.50. The first staging source seed contains 123 immutable objects and 89,671,406 bytes, all read back and SHA-256 verified; see its [report](../projects/research-workspace/verification/serverless-r2-source-seed.json).
