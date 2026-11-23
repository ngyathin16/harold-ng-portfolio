# Free-first hosting decision

Updated November 23, 2026, Asia/Hong_Kong.

The owner wants this passion project to cost nothing, or as little as practical. Supabase is optional. Target additional hosting spend is $0; no paid plan or recurring spending ceiling has been approved. Keep the existing Cloudflare portfolio hosting. Oracle and Cloudflare R2 accounts are now configured; the access checks below passed on November 23.

## Recommended first attempt

Try one Oracle Cloud Always Free Ampere A1 VM, running ordinary PostgreSQL, the existing API and a separate search-worker process. Keep private artifacts on persistent disk and serve them through the API's publication checks. This reuses the relational model and solver without Oracle's database product or a Supabase subscription.

Oracle currently lists 2 OCPUs, 12 GB RAM and 200 GB total block storage for Always Free accounts. Allocation can fail for lack of regional capacity, and idle VMs can be reclaimed. Signup requires card verification. Confirm the console marks the actual allocation as Always Free; do not depend on temporary trial credits. These are eligibility limits, not a performance guarantee. [Oracle resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [signup FAQ](https://www.oracle.com/cloud/free/faq/)

This means **self-managed Postgres**: we own patching, backups, restoration and disk monitoring. It is a proposed cost tradeoff from the PRD's managed-database default, not a deployed service or a claim that all release criteria pass. Keep database and artifact backups off the VM; a second directory on that VM is not a recovery copy.

## Capacity and alternatives

The recorded local run retained two publications and the general/Jazz result sets. Source relations used 232,062,976 bytes; application relations used 814,432,256 bytes, including 812,752,896 bytes for result rows and indexes. That is about **1.05 GB of relations**, before whole-database overhead, temporary work and growth. General JSON and CSV exports together added about 65 MB compressed; source artifacts used about 90 MB per publication. These are measurements from [the local report](../projects/research-workspace/verification/service-local.json), not minimum requirements or monthly cost forecasts. Peak memory has not been measured.

| Option | Current offer | Fit for this application |
| --- | --- | --- |
| Supabase Free | 500 MB database; Pro starts at US$25/month. | The measured result cache alone exceeds Free. A separate worker is still needed. [Pricing](https://supabase.com/pricing) |
| Neon Free | 1 GB per project and 100 CU-hours/month. | Very little or no database headroom at the measured size. The worker polls Postgres every two seconds, so scale-to-zero savings cannot be assumed. [Pricing](https://neon.com/pricing) |
| Railway Free / Hobby | Free has 0.5 GB RAM per service, 0.5 GB volume and US$1 monthly usage credit; Hobby has a US$5 minimum plus excess usage. | Free disk is insufficient; US$5 is not a cap for the database, API and worker together. [Pricing](https://railway.com/pricing) |
| Small paid VM | Hetzner's published European CX23 price is EUR5.49/month plus EUR0.50 for IPv4, before tax and optional backups. | Potential fallback, but the public catalog currently marks cost-optimized capacity unavailable. Obtain an orderable quote before selecting it. [Price schedule](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), [IP pricing](https://docs.hetzner.com/general/infrastructure-and-availability/ipv4-pricing/), [availability](https://www.hetzner.com/cloud/) |

## Deployment profile to validate

- Use the existing Python 3.12 container image and Postgres 17. Apply migrations 001, 002, 003, service 002, and 005. Plain Postgres needs the compatibility roles described in the backend README; skip Supabase-only storage migration 004.
- Give the API and worker separate login roles inheriting `research_api` and `research_worker`. Keep the database private. Expose only HTTPS through a reverse proxy, with proxy trust restricted to that ingress.
- Mount one persistent artifact directory into API and worker containers at `RESEARCH_ARTIFACT_ROOT`. API access should be read-only; the importer and worker need write access. The importer uses `--local-storage`. Never expose that directory as an unrestricted static web root.
- Start by measuring one worker and a small queue on the chosen VM. Keep the complete-result limit high enough for the general Jhin query. Concurrency, time limits and shorter result retention can be tuned after measurement; changing them does not waive the PRD's two-worker/runtime acceptance criteria. Source publications, frozen rules and saved query definitions stay retained.
- Schedule `python -m service.worker --cleanup`. Monitor free disk and database growth; expiry settings do nothing without cleanup. PostgreSQL can reuse deleted space without immediately reducing its allocated filesystem size. Per-job limits and expiry are not an aggregate storage cap; capacity admission needs to be verified before opening anonymous submissions.
- Use a filesystem that supports same-directory hard links for exclusive artifact publication, such as ext4 or XFS. Interrupted uploads cannot publish a partial final object. Keep recovery copies for host or disk failure; filesystem atomicity is not a backup.
- Build and test on the actual CPU architecture, then run the uncached general and Jazz queries, exact recounts, full exports, HTTPS browser journeys, concurrent reads and a backup restoration. Measure first-run Numba compilation as well: cold startup must fit worker lease/deadline behavior before launch. The x86 Windows timing is not an ARM hosting benchmark.

## Verified access and current blocker

Oracle API authentication passed, and the configured region matches the tenancy's home region, `ap-singapore-1`. The initial inventory found no compute instances or child compartments. Credentials remain in the owner's private local configuration outside this repository.

The R2 bucket passed an authenticated list, temporary object upload, byte-for-byte download and deletion. The test object was removed. Its public r2.dev endpoint is disabled and it has no custom domains. This verifies storage access; scheduled backups and a hosted restoration are not implemented by this check.

Oracle's compute capacity report returned `OUT_OF_HOST_CAPACITY` for `VM.Standard.A1.Flex` at both 2 OCPUs/12 GB and 1 OCPU/6 GB in the home region's availability domain. No server or paid resource was created. Recheck capacity before provisioning within Always Free limits. Do not switch to a paid shape or a non-home region as an automatic fallback. A paid alternative still needs an orderable quote and an agreed monthly ceiling.

After capacity becomes available, provision the VM, configure the services and private R2 backups, and complete the hosted workload, HTTPS and recovery checks. Account signup and credential collection do not need to be repeated.

## Bounded capacity monitoring

The owner selected the free-only path. The [Windows capacity monitor](../projects/research-workspace/ops/README.md) checks both A1 sizes every three hours for three days using the existing private credentials. It writes local status/history and attempts a Windows notification on newly observed availability. It does not create servers, reserve capacity, upgrade the account, or select paid alternatives. A deadline check prevents cloud requests after expiry, including delayed runs after sleep.

This is local automation: the PC must be on and the owner signed in. Capacity observations are not reservations. The monitor is independent of the application and does not resolve the remaining deployment or release gates.

Installed and verified on November 23, 2026 at 14:39 Hong Kong time. The first scheduled-task invocation exited successfully and reported both sizes unavailable. The next check is at 17:39; cloud checks end on November 26 at 14:39. The task then records expiry locally. Credentials, schedule state and reports stay outside Git.
