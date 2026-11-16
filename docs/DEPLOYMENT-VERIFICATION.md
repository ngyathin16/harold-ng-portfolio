# Deployment verification

Updated: 16 November 2026, Asia/Hong_Kong.

The portfolio is live at **[https://haroldng.dev](https://haroldng.dev)** on Cloudflare Workers Static Assets. The visible footer update passed hosted route, redirect, metadata, browser, and three mobile Lighthouse checks. Hong Kong and an independent US runner both passed the hosted suites. DNSSEC delegation is propagating as a nonblocking follow-up.

The earlier [local verification](VERIFICATION.md) retains its original scope and dates. Operational commands are in the [deployment runbook](DEPLOYMENT.md).

## Production release

| Field | Verified visible-update release |
| --- | --- |
| Repository | `ngyathin16/harold-ng-portfolio` |
| Production origin | `https://haroldng.dev` |
| Worker | `harold-ng-portfolio` |
| Git commit | [`7c14102a1617a742ee9bc4ce074b2c1534b6720e`](https://github.com/ngyathin16/harold-ng-portfolio/commit/7c14102a1617a742ee9bc4ce074b2c1534b6720e) |
| Cloudflare version | `9dd0e805-46c5-43f2-9c48-fd5a199c2389` |
| Cloudflare deployment | `2c8189f3-7a04-49aa-9d6e-13bd1e518a31`, 100% traffic |
| Workers build | `b354fc06-e61f-46e9-89de-46d20cc3107e`, successful |
| Known-good recovery version | `9dd0e805-46c5-43f2-9c48-fd5a199c2389` |
| Wrangler / compatibility date | `4.162.0` / `2026-11-16` |

The visible footer update in [pull request #4](https://github.com/ngyathin16/harold-ng-portfolio/pull/4) passed the normal GitHub/Workers flow. A clean browser confirmed `© 2026 Harold Ng` on the live homepage at 14:12 Asia/Hong_Kong. Evidence is retained in `artifacts/hosted/visible-update.json` and `artifacts/hosted/footer-update.png`.

The initial production release was commit `84218be85fc0ac23b2c38a06f66ae2cb0eba9b04`, version `eac08de8-aca5-4058-b692-8c1de4390c18`, deployment `847f3419-c464-4276-97fd-b240ac886329`. It also passed hosted checks and remained active during the failed-change gate proof below.

## Ownership, cost, and renewal

| Item | Recorded evidence |
| --- | --- |
| Domain and owner | Harold confirmed purchase of `haroldng.dev` in his Cloudflare account and registration email verification |
| First-year purchase | Harold confirmed the purchase email shows US$8.20 for one year |
| Current annual renewal | US$12.20 at checkout; future registry prices can change |
| Auto-renew | Harold confirmed enabled |
| Workers plan | Harold confirmed the current plan is Free |
| Zone plan | Cloudflare API confirmed active Free Website plan, US$0 |
| Actual HKD card settlement | Not recorded; no exchange rate or final HKD amount is asserted |
| Registration | `2026-11-16T05:31:21.526Z`, confirmed by registry RDAP |
| Expiration | `2027-11-16T05:31:21.526Z`, or 16 November 2027 at 13:31 Asia/Hong_Kong |
| Renewal review | By 2 October 2027, 45 days before expiry; date arithmetic checked. No scheduled reminder is claimed. |
| Nameservers | `alexandra.ns.cloudflare.com`, `paul.ns.cloudflare.com` |

[Google Registry RDAP](https://pubapi.registry.google/rdap/domain/haroldng.dev) confirms the registration and expiration. `harold.work` was excluded because its registry record showed an existing registration. Receipts, payment details, and personal registrant data remain outside the public repository. Keep future renewal spending within the agreed HK$200 annual ceiling and review the price before billing.

DNSSEC was enabled at approximately 13:46 Asia/Hong_Kong on 16 November. Cloudflare still reported `pending` at 14:03, and the latest registry observation showed `zoneSigned: true`, `delegationSigned: false`. This is a propagation follow-up while DNS and HTTPS work. Cloudflare Registrar checks CDS/CDNSKEY records and submits the delegation to the registry; first activation can take one to two days. Recheck after 13:46 on 18 November 2026. Do not reset DNSSEC or add manual DS records during this normal Registrar activation. [Cloudflare Registrar DNSSEC process](https://developers.cloudflare.com/registrar/get-started/enable-dnssec/)

## Build and deployment configuration

Cloudflare OAuth authentication succeeded. Harold connected the canonical GitHub repository. Disabled dashboard Builds controls supplied no tooltip, so configuration was completed through the official `cf@1.0.0-beta.32` CLI on portable Node `24.22`. Its additional OAuth permissions covered Builds configuration, DNS, and redirects. No broad billing or domain-purchase permissions were requested. Wrangler remains locked at `4.162.0` in the application.

The Builds API saved these settings, and a subsequent read confirmed them:

| Setting | Saved value |
| --- | --- |
| Repository / production branch | `ngyathin16/harold-ng-portfolio` / `main` |
| Build | `npm ci && npm run build:deployment` |
| Production deploy | `npm run deploy:production` |
| Branch preview | `npm run deploy:preview` |
| Production build variables | `NODE_VERSION=24`, `SKIP_DEPENDENCY_INSTALL=1`, `SITE_URL=https://haroldng.dev`, `APPROVED_SITE_URL=https://haroldng.dev` |
| Preview build variables | `NODE_VERSION=24`, `SKIP_DEPENDENCY_INSTALL=1`; both origin variables absent |
| Automatic builds / preview builds | Enabled |
| Production provider endpoint / preview URLs | `workers_dev: false` / `preview_urls: true`; production provider hostname returned HTTP 403 with a 17-byte body, not a redirect |

GitHub's `APPROVED_SITE_URL` variable is also `https://haroldng.dev`. The active `main` rule requires an up-to-date branch, a pull request, and successful `release-checks` from GitHub Actions app `15368`. It applies to administrators, requires zero additional reviewer approvals, and disables force pushes and branch deletion. Production deployment separately requires a merged pull request and successful checks for the exact commit.

The apex Custom Domain serves the Worker. A proxied `www` A record points to `192.0.2.1`; one Single Redirect sends HTTP apex and `www` requests to HTTPS apex with status 301 and preserves the path and query. The first domain-attachment attempt returned error `100124` before the Worker had a production deployment. The gated first deployment resolved that prerequisite.

## Verification performed

| Check | Result and scope |
| --- | --- |
| Clean dependency installation | Fresh `npm ci` passed with 393 packages; npm reported zero vulnerabilities |
| Release tests | 21 Node tests passed after the preview and native trailing-slash redirect checks were corrected |
| Production build | `https://haroldng.dev` build passed with zero errors, warnings, or hints |
| Hosted production HTTP suite | Passed 10 paths and 13 redirect journeys on version `9dd0e805-46c5-43f2-9c48-fd5a199c2389`; report at `artifacts/hosted/http-check.json` |
| Hosted production browser suite | Passed 12 page/viewport combinations and 28 local links at 360, 768, and 1440 px; zero axe violations and no reported errors; `artifacts/portfolio/browser-check.json` |
| HTTPS and canonical redirects | Apex and `www` HTTPS work; HTTP and `www` normalize to HTTPS apex with path/query preserved |
| Metadata and assets | Production custom-origin metadata, indexability, PDF/ZIP bytes, image/font loading, security headers, and caching passed the applicable suites |
| Test locations | Hong Kong local runner, confirmed by Cloudflare trace `loc=HK`, `colo=HKG`; independent GitHub runner in the US, trace `loc=US`, `colo=DFW` |
| Hosted mobile Lighthouse | Three runs passed with performance 100 and accessibility 100 in every run; medians 100/100 |

The final production HTTP and browser runs were recorded at 14:12 Asia/Hong_Kong on 16 November. Both reports identify commit `7c14102a1617a742ee9bc4ce074b2c1534b6720e` and version `9dd0e805-46c5-43f2-9c48-fd5a199c2389`. Host and protocol changes require permanent 301/308 responses. Cloudflare's native trailing-slash normalization returned 307. The verifier permits that response only for an exact slash change on the canonical HTTPS host with the query preserved. All other redirect checks remain strict. This records observed platform behavior rather than asserting every hop is permanent.

The initial implementation passed [GitHub Actions run 43802316049](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/43802316049) at commit `bf4a5bc1e2a66ada14e1ab7a317a6b1fb1d41e26`, before the production launch. That run included isolated preview and production-style fixture builds plus browser checks. It is historical CI evidence, not a measurement of the final origin.

Local resource limits affected preparation: the C: drive filled during dependency installation, so verification moved to an isolated D: checkout. One local production-style retry ran out of memory. Subsequent production builds and browser checks passed. Those local failures do not describe production behavior.

## Hosted mobile Lighthouse

Three runs against `https://haroldng.dev/` completed at approximately 14:13 Asia/Hong_Kong on 16 November. All measured commit `7c14102a1617a742ee9bc4ce074b2c1534b6720e`, version `9dd0e805-46c5-43f2-9c48-fd5a199c2389`.

| Run | Performance | Accessibility | FCP / LCP, ms | Total blocking time, ms | Cumulative layout shift |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 100 | 100 | 876.5 / 876.5 | 0 | 0.017740 |
| 2 | 100 | 100 | 855.5 / 855.5 | 9 | 0.004732 |
| 3 | 100 | 100 | 848.4 / 848.4 | 0 | 0.004732 |
| Median score | 100 | 100 | | | |

Lighthouse `13.6.0` used headless Edge `157.0.4341.57` and Node `24.14` on the Hong Kong Windows 11 runner, with an Intel Core Ultra 9 285K and 24 logical processors. Preflight samples recorded 11–12% CPU use and 15.24 GiB available memory. Mobile emulation used a 412 × 823 viewport, device scale factor 1.75, simulated 150 ms RTT, 1,638.4 Kbps throughput, and 4× CPU slowdown. Storage was cleared per navigation. These are lab measurements of the hosted origin, not measurements from a physical phone or real-user traffic.

Every run received the homepage over HTTPS/HTTP 3 with status 200 and 8,532 decoded bytes. Recorded document transfer sizes were 3,711, 3,684, and 3,677 bytes. No failed network requests, runtime errors, or run warnings were reported. Cloudflare injected its `static.cloudflareinsights.com` beacon and `/cdn-cgi/rum` request into the hosted page; those requests were included in the measurements. The source artifact ships no application JavaScript, but the hosted page contains this provider script.

All individual HTML/JSON reports and the full settings/results are retained in `artifacts/lighthouse/mobile-{1,2,3}.{html,json}` and `artifacts/lighthouse/summary.json`. The 100/100 medians exceed the launch target of 90/90.

## Independent network verification

[GitHub Actions run 43815201056](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/43815201056) passed against the same final origin, source commit, and Cloudflare version. Cloudflare trace identified country `US` and edge `DFW`; the HTTP reports' Ray identifiers also ended in `DFW`. No client IP is retained in the evidence.

The independent HTTP suite passed 10 paths and 13 redirect journeys, including the raw custom 404. Chromium `158.0.8127.18` with axe `4.14.0` passed 12 page/viewport checks at 360, 768, and 1440 px and 28 links, with no axe violations or reported errors. Network, HTTP, and browser observations completed around 14:13 Asia/Hong_Kong on 16 November. Together with the Hong Kong runner, these establish two actual test networks; they do not claim global uptime monitoring.

The downloaded CI artifact and inspected summary are retained under `artifacts/independent-network/43815201056/`, with network observations, HTTP/browser reports, logs, screenshots, and hashes.

## Isolated public preview

| Field | Value |
| --- | --- |
| URL | [launch-review](https://launch-review-harold-ng-portfolio.ngyathin16.workers.dev) |
| Preview ID | `a8ff75e1ae574fa9aec776cf5c89b1c1` |
| Deployment | `09792254-accb-4d14-970b-513cf898fd09` |
| Source commit | `43884702b42e72826abef93d6604870546362f28` |

The first HTTPS request failed during certificate provisioning; later requests succeeded. At 13:32 Asia/Hong_Kong, the preview HTTP suite passed 10 paths. At 13:34, Edge `157.0.4341.57` and axe `4.14.0` passed 12 page/viewport combinations and 28 links with no axe violations or reported errors. Rendered and provider noindex checks passed.

Initial checks exposed two verifier assumptions. Cloudflare's generated preview handler returns a plain raw 404 but serves the site's custom 404 and asset headers for browser navigation. The preview verifier now records both behaviors; production still requires the custom raw 404. The browser runner also encountered a navigation-canceled favicon request. It excludes `net::ERR_ABORTED` while retaining other asset-failure and explicit image/font checks. No site workaround was added for either finding.

## Failed-change gate proof

[Draft pull request #3](https://github.com/ngyathin16/harold-ng-portfolio/pull/3) deliberately emptied the Relay Desk title at commit `87eadb358b3ed34a677419a7e5f25ce136954386`. The required GitHub check failed content-schema validation in [PR run 43813630040](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/43813630040) and [branch run 43813576870](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/43813576870). Cloudflare preview build `0b596048-c6e9-41c1-a086-c60d8af655e3` failed the same schema check.

The PR's merge state was blocked. Production remained at commit `84218be85fc0ac23b2c38a06f66ae2cb0eba9b04`, deployment `847f3419-c464-4276-97fd-b240ac886329`, version `eac08de8-aca5-4058-b692-8c1de4390c18`, with 100% traffic before and after the proof. The PR was closed without merging, its branch was deleted, and its preview was confirmed removed at 14:01 Asia/Hong_Kong. Evidence is retained in `artifacts/gate-proof/summary.json`, snapshots, and logs.

Draft status independently prevents merging. The failed required checks and enforced branch protection were verified separately; no merge or direct push to `main` was attempted. This test demonstrates that the failed branch build left the active production release unchanged.

## Isolated recovery rehearsal

The rehearsal passed on 16 November, approximately 13:26 to 13:31 Asia/Hong_Kong. It used source commit `43884702b42e72826abef93d6604870546362f28` and a separate copy of the noindex preview artifact. Temporary Worker `harold-ng-portfolio-recovery` had no production routes, bindings, or paid resources.

| Stage | Version | Deployment | Observed result |
| --- | --- | --- | --- |
| A | `554f8972-5786-4336-b0c3-8af3e7fd2fd2` | `516c8cfd-b1e4-46e0-823b-1fcfe891e570` | Original homepage |
| B | `9dc6d09f-be82-47c6-8dbb-10359b4c1f2d` | `e9dcfbaa-a7e3-4e37-b3ed-c4f300531f46` | Visible recovery marker |
| Restored A | `554f8972-5786-4336-b0c3-8af3e7fd2fd2` | `2377d08b-1313-4b4b-9fd3-e47c26f102b1` | Original homepage and marker absent |

```sh
npx wrangler rollback 554f8972-5786-4336-b0c3-8af3e7fd2fd2 --yes --config artifacts/rollback/wrangler.json --message "Isolated recovery rehearsal restore exact baseline A"
```

All 25 served files matched their expected complete bytes and SHA-256 hashes at each stage. The checks covered the homepage, both case studies, custom HTTP 404, CSS, fonts, images, PDF, ZIP, robots, sitemap, favicon, and font licenses. The restored manifest exactly equaled A. Noindex, mutable-file revalidation, and fingerprinted immutable caching passed. Browser screenshots confirmed the visible B marker appeared and disappeared as expected.

The temporary Worker was deleted; the API confirmed absence with HTTP 404/provider code `10090`. Evidence remains in `artifacts/rollback/result.json`, manifests, command logs, HTTP/browser reports, and screenshots. This was an isolated static-asset rollback. It did not change production, restore DNS, or test multiple networks. The verified production version above is recorded separately as a known-good recovery target.

## Launch acceptance and remaining evidence

| Requirement | Status | Evidence or remaining item |
| --- | --- | --- |
| D1: Ownership and budget | Passed with billing limit recorded | Domain, verified registration email, approved US$8.20 purchase and US$12.20 current renewal, auto-renew, expiration/review dates, and Free Workers plan confirmed. Actual HKD card settlement is unrecorded; no converted amount is invented. |
| D2: Public access | Passed | Apex/www HTTPS and hosted suites passed from the Hong Kong runner and independent US/DFW GitHub runner. |
| D3: Routes and redirects | Passed | Final release passed 10 paths and 13 redirect journeys, including custom raw 404, security/cache headers, and path/query preservation. Native canonical-host slash normalization uses 307 as documented above. |
| D4: Visitor journey | Passed | Final-release browser suite passed at all required widths with no axe violations or reported errors. |
| D5: Search metadata | Passed | Final production metadata/indexability and preview/404 noindex checks passed. |
| D6: Update delivery | Passed with recorded proof limits | Protected GitHub/Workers flow launched production, failed-change proof preserved it, and a separate merged footer update was deployed and visibly confirmed. The draft negative test did not attempt a merge or direct push. |
| D7: Recovery | Passed | Known-good production version recorded; isolated A/B/rollback and complete asset restoration passed; temporary Worker removed. |
| D8: Quality and cost | Passed | Final hosted browser suite and three mobile Lighthouse runs passed; performance/accessibility medians 100/100. Free Workers plan confirmed. Usage review remains a maintenance task. |

Follow up DNSSEC propagation after 13:46 Asia/Hong_Kong on 18 November 2026. Record the actual HKD card settlement when available, review free-plan usage monthly, and review renewal by 2 October 2027. These follow-ups do not change the recorded successful launch checks. No synthetic HKD total or unperformed network check is asserted.

Reports under `artifacts/hosted/`, `artifacts/portfolio/`, `artifacts/lighthouse/`, `artifacts/independent-network/`, `artifacts/gate-proof/`, and `artifacts/rollback/` are ignored by Git. Retain per-release copies or CI artifact links before subsequent runs overwrite them. Do not publish credentials, receipts, or personal registrant details.
