# Serverless feasibility and cost worksheet

Prepared November 25, 2026, Asia/Hong_Kong. Currency USD. This is a dated planning worksheet, not a provider selection or a measured hosted bill. Experiment spending limit and monthly production limit remain **unagreed**. No resources were provisioned. Do not infer permission to spend from the numeric workload examples or test fixtures.

## Provider decision pending measured qualification

| Candidate | Proposed configuration | Known commercial inputs | Measurements still needed |
| --- | --- | --- | --- |
| Neon Launch, evaluated first | PostgreSQL 17, AWS Singapore `aws-ap-southeast-1`, direct endpoint for operators; compare direct and pooled runtime endpoints. Start qualification with 0.25 CU minimum and 1 CU maximum, 8 GB application storage planning allowance. | $0.106/CU-hour, $0.35/GB-month database storage, $0.20/GB-month history changes. Additional branches and snapshots are separate. Launch includes 500 GB/month public transfer, then $0.10/GB. | Exact orderable endpoint/compute sizes, connection ceiling, billed storage including history/branches, clean import, search growth, TLS/permissions, Hong Kong browsing and Cloud Run round trips. |
| Cloud SQL Enterprise, alternative | PostgreSQL 17, Singapore `asia-southeast1`, one zonal dedicated vCPU, 3.75 GiB RAM, 10 GiB SSD; private IP plus Direct VPC egress or authenticated TLS public endpoint must be separately priced/qualified. | Region-specific compute, storage, backup, IPv4/network and possible network components; quote pending. No free-plan assumption. | Same application/restore matrix, connection exhaustion, measured CPU sizing, configuration orderability, selected network path and calculator export. |
| Neon Free, compatibility experiment only | Same region and PG17 where offered | 1 GB storage and 100 CU-hours/project/month. | Clean source import allocated 125,466,291 bytes locally; previous full-result allocation reached 1.758 GB before headroom. Two 2 GiB result reservations plus 1 GiB headroom exceed this tier, so this is **not demonstrated to fit**. Do not discard retained data to fit it. |

Sources: [Neon pricing](https://neon.com/pricing), [Neon regions](https://neon.com/docs/introduction/regions), [Cloud SQL pricing](https://cloud.google.com/sql/pricing). Provider choice remains pending; the Terraform module deliberately does not provision an unqualified database.

## Workload assumptions

The illustrative month is 30 days (720 hours). Use real billable instance time after rehearsal; simultaneous HTTP requests share instance time. Inputs below are planning assumptions, not extrapolated local solver benchmarks.

| Quantity | No visitors, schedules enabled | Expected illustration | Configured compute envelope |
| --- | ---: | ---: | ---: |
| Browse requests/month | 0 | 100,000 | Read quota 600/minute; unmetered infrastructure probes mean no complete public request-cost ceiling |
| New searches/month | 0 | 300 | At most 4 launch attempts/minute; 2 outstanding executions globally |
| Paid attempts/search | 0 | 1.1 (330 total) | Application at most 3; platform Job retries 0; launch attempts also bounded |
| Search allocation | 2 vCPU, 2 GiB | 2 vCPU, 2 GiB | Same |
| Assumed search billed duration | 0 | 180 seconds including startup, compilation, search, persistence, shutdown | 300-second task timeout; 120-second solver target remains separate |
| API billed instance-seconds | 0 | 20,000 | At most 2 instances continuously busy, subject to platform scaling behavior |
| Dispatcher executions | 8,640 at 5-minute cadence | 8,640 plus 330 wakeups | At most 2 instances; 180-second request timeout |
| Assumed idle/expected dispatcher instance-seconds | 43,200 (5 seconds/run) | 44,850 | Measure empty reconciliation; maximum per-call bound is not the expectation |
| Operator executions | 30 backups + 30 prunes + 30 cleanups | Same, plus explicit import/restore drill | 1 vCPU/4 GiB; 1,800-second task timeout; application deadline 1,500 seconds |
| Assumed operator billed seconds total | 21,600 (backup 600s, prune/cleanup 60s each/day) | Same | 162,000 seconds for the three schedules at platform timeout, excluding manual work |
| Database wake hours | 720 conservative | 720 conservative | 720 at reviewed maximum CU |
| Database/result growth | No new results; operator history writes remain | Measure bytes per general/Jazz result; reserve 2 GiB per accepted run plus 1 GiB headroom | Capacity admission stops allocation; it does not promise a monthly storage price |
| Backup retention | 7 daily + 4 weekly, overlapping calendar sets deduplicated | Same | Never remove the last verified usable set |

A five-minute reconciler can keep a database with a five-minute idle timeout awake indefinitely. At 0.25 CU continuously awake, Neon compute alone is `720 × 0.25 × 0.106 = $19.08/month`; at 1 CU it is $76.32. Eight GB storage adds $2.80 before history, snapshots and transfer. These are arithmetic illustrations, not a measured Neon quote or an assertion that either compute size passes load.

## Billing inputs and formulae

| Component | Unit price / calculation | Remaining qualification |
| --- | --- | --- |
| Cloud Run API + dispatcher, Singapore | Request-billed vCPU-second $0.0000336; GiB-second $0.0000035; requests $0.40/million. Allocations 1 vCPU/0.5 GiB each. | Measured aggregate billed instance time, starts, shutdown and requests. |
| Cloud Run search and operator Jobs, Singapore | vCPU-second $0.0000216, GiB-second $0.0000024; at least 60 billed seconds per execution. | Platform startup and termination observations; count duplicates/rejections that create executions. |
| Cloud Tasks | $0.40/million billable operations after available allowance; count enqueue, delivery and retries, chunked by payload size. | Real operation count. |
| Cloud Scheduler | 4 schedules × $0.10/31 days gross; 3 schedules/account/month allowance when unused elsewhere. Paused schedules still count. | Account-wide allowance remaining. |
| Artifact Registry | GiB-hours × $0.000136986 above available 0.5 GiB allowance; retain current and compatible rollback images. | Actual service/operator image layers and history. |
| Build | No Cloud Build resource in this module; use existing reviewed CI/local image build. | Record GitHub build minutes and any external build/storage charges separately; no universal free-CI claim. |
| Secret Manager | 18 active versions × approximately $0.06/month gross; secret accesses $0.03/10,000 above allowance. | Versions retained during rotation and starts per identity. |
| Logs and monitoring | Logs $0.50/GiB ingested above 50 GiB/project/month; metric/alert usage per selected monitoring features. | Log volume, alert charges and retention export. Never log session capabilities or DSNs. |
| Neon | `CU-hours × 0.106 + database GB-month × 0.35 + history GB-month × 0.20 + branch/snapshot/transfer charges`. | Actual billable storage and regional plan; replace with Cloud SQL quote if selected. |
| Google internet/network transfer | Google-to-Neon result inserts + API response bytes + R2 backup uploads × selected source/destination SKU. | Region/destination quote and measured wire bytes; **unpriced**, not zero. |
| Neon-to-Google | Database reads/backups counted under plan transfer allowance then applicable overage. | Measure each direction; cross-provider transfers are not same-cloud free transfers. |
| R2 Standard | GB-month $0.015; Class A $4.50/million, Class B $0.36/million; billable units round up. R2 egress is free. | Retained immutable objects + complete/partial backups + reports. Include PUT/LIST/GET verification and multipart operations. |
| Database networking, provider history, DNS, optional state backend | Exact quote for selected deployment | **Unpriced** until provider/network/hostname are chosen. This module adds no load balancer/VPC/NAT implicitly. |

Pricing references checked November 25: [Cloud Run](https://cloud.google.com/run/pricing) (Singapore region data in the page, not the default Iowa table), [Tasks](https://cloud.google.com/tasks/pricing), [Scheduler](https://cloud.google.com/scheduler/pricing), [Registry](https://cloud.google.com/artifact-registry/pricing), [Secrets](https://cloud.google.com/secret-manager/pricing), [Observability](https://cloud.google.com/products/observability/pricing), [R2](https://developers.cloudflare.com/r2/pricing/).

Cloud Run gross arithmetic with these assumptions: idle services `$1.52712`, scheduled operators `$0.67392`; expected services `$2.2924475`, searches `$2.8512`, operators `$0.67392`, plus roughly `$0.043588` requests. These subtotals exclude database, transfer and the other rows and must not be presented as total application prices. Free compute allowances are shared across the billing account, with credits valued at Tier 1 prices; do not subtract a fresh allowance for each service or Job. Record both gross and actual remaining allowances.

The two outstanding-execution limit bounds simultaneously live search containers. Four launches/minute permits at most 172,800 launches in 30 days. A conservative billed-time envelope allowing minimum-one-minute charges for short failures is `2 × 2,592,000 + 172,800 × 60 = 15,552,000` execution-seconds: `$746.496` gross search compute at 2 vCPU/2 GiB. This intentionally overcounts and is not a predicted bill. Continuously busy API/dispatcher instances, network traffic, manual operators and unrelated project usage are additional. A monthly application spending ceiling is **not implemented by these concurrency limits**.

## Decision record required before provisioning

Record the exact project/billing account, provider plan, region pair, orderable configuration, clean import and billed bytes, measured connection/latency/permissions, all unpriced rows, experiment maximum USD, monthly maximum USD, alert recipient, quote date and approving decision. Terraform requires this review object, but a string reference does not substitute for the underlying evidence. Budget alerts at 50/90/100% are notifications; no hard cap is asserted. Assess any separately offered cap's actual coverage, delay and failure behavior before relying on it. [Google budget behavior](https://docs.cloud.google.com/billing/docs/how-to/budgets).

After a 24-hour no-visitor hosted observation, replace assumptions with execution IDs, billable seconds, database wake periods, connections, R2 requests and available billing data. Label delayed usage and estimated charges separately. Only then reconcile the full cost worksheet and seek the production spending decision.
