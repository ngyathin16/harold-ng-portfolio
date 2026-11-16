# Deployment verification

Record date: 16 November 2026, Asia/Hong_Kong.

This record separates deployment preparation and an isolated public preview from a production launch. Cloudflare OAuth authentication succeeded on 16 November 2026, and the `launch-review` preview is deployed in Harold's account. Harold confirmed the purchase of `haroldng.dev` and email verification after selecting the one-year Cloudflare quote of US$8.20, with US$12.20 current annual renewal. The domain's Cloudflare zone is active. No custom-domain production deployment is established. The earlier [local verification](VERIFICATION.md) keeps its original scope and dates.

## Preparation evidence

The implementation adds locked Wrangler tooling, static asset configuration, production and preview release checks, GitHub verification, hosted HTTP and browser checks, and three-run mobile Lighthouse reporting. See [the deployment runbook](DEPLOYMENT.md) for commands and account settings.

The implementation was submitted in [pull request #1](https://github.com/ngyathin16/harold-ng-portfolio/pull/1). [GitHub Actions run 43802316049](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/43802316049) passed for commit [`bf4a5bc1e2a66ada14e1ab7a317a6b1fb1d41e26`](https://github.com/ngyathin16/harold-ng-portfolio/commit/bf4a5bc1e2a66ada14e1ab7a317a6b1fb1d41e26). That run checked both preview and production-style builds, release tests, the browser journey at all three widths, and verification artifacts. The production-style build used the isolated `https://portfolio-ci.invalid` fixture. It was not deployed.

| Check | Observed result |
| --- | --- |
| Fresh `npm ci` in the isolated D: checkout | Passed; 393 packages installed, npm reported zero vulnerabilities. |
| `npm run test:release` | Passed; 20 Node tests after the preview-verifier correction. The initial preparation run passed 18 tests. |
| Preview `npm run build:deployment` and `npm run check:release -- --mode preview` | Passed locally. |
| Local production build for `https://haroldng.dev` | Passed with zero errors, warnings, or hints. The GitHub `APPROVED_SITE_URL` variable is set to this origin. This is not a production deployment. |
| `npx wrangler deploy --dry-run` | Passed locally; 33 static assets. No upload or deployment performed. |
| Browser suite against local Workers | Passed; 12 page/viewport combinations and 28 local links. The final journey checks at every width also passed in the linked GitHub run. |
| Wrangler command/schema checks | Version `4.162.0`, preview command help, and preview configuration schema verified. |
| `npx wrangler whoami` | Initially unauthenticated. Browser OAuth subsequently succeeded on 16 November 2026. |
| Hosted preview HTTP checks | Passed 10 paths after correcting the verifier's handling of the provider-generated preview 404. |
| Hosted preview browser checks | Passed 12 page/viewport combinations and 28 local links at 360, 768, and 1440 px, with zero axe violations and no reported errors. |
| Hosted Lighthouse and final-origin checks | Not run. The custom domain is not serving a production release. |

GitHub `main` branch protection was applied through the API and read back. It requires `release-checks`, an up-to-date branch, and a pull request, with zero additional reviewer approvals. Administrators are subject to the rule. Force pushes and branch deletion are disabled. This confirms the configured gate. A blocked failing change and a visible production update remain launch evidence to collect.

The initial dependency installation encountered insufficient space on the C: drive. Implementation and clean-install verification moved to an isolated checkout on D:. A subsequent local production-style build retry ran out of memory. Both build modes passed in the linked GitHub run. These local resource limits do not establish a hosted failure or result.

| Field | Recorded value |
| --- | --- |
| Repository | `ngyathin16/harold-ng-portfolio` |
| Preparation base commit | `e1f0df7` |
| Prepared Worker name | `harold-ng-portfolio` |
| Wrangler dependency | `4.162.0`, exact version in the npm lockfile |
| Compatibility date | `2026-11-16` |
| Verified implementation commit | `bf4a5bc1e2a66ada14e1ab7a317a6b1fb1d41e26` |
| GitHub verification | Run `43802316049` passed; main branch protection applied and read back |
| Domain | Harold confirmed `haroldng.dev` purchased after selecting the one-year US$8.20 quote, with US$12.20 current renewal, and email verified. The actual HKD charge including fees has not been recorded. |
| Registration and expiration | Registry RDAP confirms registration `2026-11-16T05:31:21.526Z` and expiration `2027-11-16T05:31:21.526Z`, both 13:31 Asia/Hong_Kong on those dates |
| Renewal review deadline | 2 October 2027, 45 days before expiry; date arithmetic checked. No scheduled reminder is claimed. |
| Cloudflare account owner and plan | Harold's account authenticated through OAuth. Zone API confirms an active Free Website plan at US$0. Workers subscription confirmation is still pending. |
| Authoritative nameservers | `alexandra.ns.cloudflare.com`, `paul.ns.cloudflare.com` |
| DNSSEC | Registry RDAP reports `zoneSigned: true`, `delegationSigned: false`; delegation is not yet active |
| GitHub integration | Harold confirmed it connected; Builds editing controls are disabled, and production/preview build commands and variables remain unconfirmed |
| Public Preview URL | [launch-review](https://launch-review-harold-ng-portfolio.ngyathin16.workers.dev) |
| Preview ID | `a8ff75e1ae574fa9aec776cf5c89b1c1` |
| Preview deployment ID | `09792254-accb-4d14-970b-513cf898fd09` |
| Preview source commit | `43884702b42e72826abef93d6604870546362f28` |
| Production URL | Not deployed |
| Production Git commit | Not deployed |
| Cloudflare production version and deployment | Not deployed |
| Known-good production version | None recorded |
| DNS and TLS test locations | Preview HTTPS returned 200 after initial certificate provisioning; Cloudflare response identified the HKG edge. Client location and a second independent network are unverified. |

## Cloudflare preview activation

The Worker `harold-ng-portfolio` exists in Harold's account. Its production deployment field remains null. Production `workers.dev` is disabled and preview URLs are enabled. The previously missing free provider subdomain `ngyathin16` was registered through the Cloudflare API before creating the isolated preview.

The first preview HTTPS request failed during certificate provisioning. A later request returned HTTP 200. The response's Cloudflare Ray identifier ended in `HKG`, which identifies the serving Cloudflare edge. It does not independently establish the client's network location or satisfy the two-location production TLS requirement.

Initial preview checks failed because Cloudflare's generated preview handler returns a plain HTTP 404 to a non-navigation request for an unknown path. Browser navigation receives the site's useful custom 404 with asset headers. The verifier now records the raw preview 404 separately and validates the custom navigation 404. Production checks still require the custom page and full headers for raw requests. This was a preview-verification mismatch; no application workaround was added.

The browser suite initially reported an aborted favicon request during navigation. The verifier now excludes requests canceled with `net::ERR_ABORTED` while retaining other asset-failure checks and explicit image/font checks. The corrected suite passed. This was a test-runner finding, not a production-site fix.

| Hosted preview check | Recorded result |
| --- | --- |
| HTTP suite, 16 November 2026 at 13:32 Asia/Hong_Kong | Passed 10 paths, including the separate raw and navigation 404 behaviors; `artifacts/hosted/http-check.json` |
| Browser suite, 16 November 2026 at 13:34 Asia/Hong_Kong | Passed 12 page/viewport checks and 28 links at 360, 768, and 1440 px; zero axe violations and empty error list; `artifacts/portfolio/browser-check.json` |
| Browser and accessibility engine | Microsoft Edge `157.0.4341.57`, axe `4.14.0` |
| Artifact/source association | Preview deployment `09792254-accb-4d14-970b-513cf898fd09`, source `43884702b42e72826abef93d6604870546362f28`; browser report includes both, while the HTTP report records the deployment and leaves its commit field unset |

The current OAuth permissions return HTTP 403 for Builds and subscription inspection. Harold confirmed the GitHub integration is connected; build settings and the Workers plan still need confirmation in the dashboard. Builds editing controls are currently disabled, with the reason still under investigation. At this checkpoint, the activation branch has not been pushed, so it has not triggered an automatic production deployment. The zone's Free Website plan does not establish the Workers subscription tier.

`harold.work` was excluded from normal registration because its registry RDAP record showed it already registered. Harold instead purchased `haroldng.dev` at the selected one-year quote and verified the registration email. The zone API reports it active. The current renewal quote does not guarantee future renewal pricing.

[Google Registry RDAP](https://pubapi.registry.google/rdap/domain/haroldng.dev) confirms registration on 16 November 2026 and expiration on 16 November 2027. Its DNSSEC fields report a signed zone without an active signed delegation. Review the renewal price and account details by 2 October 2027. Purchase confirmation and the selected USD quote are recorded; the actual payment total in HKD remains unverified.

Attaching the domain before the first production deployment returned Cloudflare error `100124`, because the Worker has no production deployments yet. The custom-domain route is now in the configuration for the first gated production deploy. Final apex and `www` TLS, redirects, and production responses remain unverified.

## Isolated recovery rehearsal

The recovery rehearsal passed on 16 November 2026, approximately 13:26 to 13:31 Asia/Hong_Kong. It used source commit `43884702b42e72826abef93d6604870546362f28` and a separate copy of the noindex preview artifact. The temporary Worker `harold-ng-portfolio-recovery` had no domain routes, bindings, or paid resources. Its provider URL was `https://harold-ng-portfolio-recovery.ngyathin16.workers.dev`.

| Stage | Version ID | Deployment ID | Browser result |
| --- | --- | --- | --- |
| Baseline A | `554f8972-5786-4336-b0c3-8af3e7fd2fd2` | `516c8cfd-b1e4-46e0-823b-1fcfe891e570` | Original homepage, no recovery marker |
| Changed B | `9dc6d09f-be82-47c6-8dbb-10359b4c1f2d` | `e9dcfbaa-a7e3-4e37-b3ed-c4f300531f46` | Visible recovery marker below the heading |
| Restored A | `554f8972-5786-4336-b0c3-8af3e7fd2fd2` | `2377d08b-1313-4b4b-9fd3-e47c26f102b1` | Original homepage restored, marker absent |

The recorded rollback command was:

```sh
npx wrangler rollback 554f8972-5786-4336-b0c3-8af3e7fd2fd2 --yes --config artifacts/rollback/wrangler.json --message "Isolated recovery rehearsal restore exact baseline A"
```

At each stage, all 25 served files matched their expected complete response bytes and SHA-256 hashes. Those files covered the homepage, both case studies, custom HTTP 404, CSS, fonts, images, PDF, ZIP, robots, sitemap, favicon, and font licenses. The restored manifest exactly matched baseline A. Noindex metadata and response headers, mutable-file revalidation, and fingerprinted immutable caching also passed. Browser screenshots confirmed the marker appeared only in B. `_headers` was treated as provider configuration, not as a publicly served file.

The temporary Worker was deleted after the rehearsal. The Cloudflare API confirmed its absence with HTTP 404 and provider code `10090`. Evidence remains in `artifacts/rollback/result.json`, the deployment logs, HTTP and browser reports, manifests, and screenshots. The launch-review preview and production Worker were unchanged.

This verifies isolated static-asset version recovery from one local runner. It does not establish production recovery, a known-good production version, DNS restoration, or availability from two networks.

## Launch acceptance

| Requirement | Status | Evidence required to complete |
| --- | --- | --- |
| D1: Ownership and budget | Partially verified | Harold confirmed `haroldng.dev` purchased and registration email verified. Registry expiration is 16 November 2027, with renewal review due 2 October 2027. The US$8.20 one-year quote and US$12.20 current renewal are recorded; the active zone's Free Website plan is US$0. Actual HKD charge including fees and Workers free-plan confirmation remain pending. |
| D2: Public access | Pending | Clean-browser result, apex and `www` certificate coverage, and DNS/TLS observations from Hong Kong and one independent network or location. |
| D3: Routes and redirects | Partially verified | Preview HTTP suite passed 10 paths, including both preview 404 behaviors and asset headers. Final-origin routes, path/query preservation, redirects, raw custom 404, security headers, and caching remain to verify. |
| D4: Visitor journey | Partially verified | Public preview passed 12 page/viewport checks and 28 links at all required widths, with zero axe violations or errors. Repeat the suite and manual contact/repository journey on the final custom origin. |
| D5: Search metadata | Partially verified | Preview noindex and custom 404 checks passed. Production metadata was built locally for `https://haroldng.dev`; final served canonicals/social/sitemap/robots and indexability remain to verify. |
| D6: Update delivery | Partially verified | Required GitHub check passed and main protection was applied/read back. Still needs a blocked failing change, visible production update through the normal workflow, and Cloudflare deployment/version matched to its Git SHA. |
| D7: Recovery | Partially verified | Isolated A/B/rollback rehearsal passed, restored all 25 served files, and temporary Worker cleanup was confirmed. A known-good production version and production recovery record remain pending. |
| D8: Quality and cost | Pending | Final-origin browser suite, three hosted mobile Lighthouse reports with conditions and median scores, resolved launch blockers, and actual free-plan usage. |

## Add a hosted verification run

For each launch or recovery run, add the actual observations below. Leave untested entries pending.

- Date and time in Asia/Hong_Kong, tester, and actual network/location.
- Exact origin, Git commit, Cloudflare version, deployment ID, and Workers build ID.
- Commands, exit results, response statuses, redirect targets, certificate coverage, and report locations.
- Browser version and viewport results, including manual external contact and repository checks.
- Lighthouse URL, three individual scores, median, browser version, throttling settings, and test network/location.
- Hosting plan and observed usage, domain renewal review date, and recovery access owner.
- Unresolved limits and their effect on launch acceptance.

Automated reports are stored under `artifacts/hosted/`, `artifacts/portfolio/`, and `artifacts/lighthouse/`. They are ignored by Git. Retain the relevant CI artifact links or attach reports to the release evidence. Do not publish authentication material, receipts, or personal registrant details.
