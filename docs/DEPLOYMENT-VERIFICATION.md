# Deployment verification

Record date: 16 November 2026, Asia/Hong_Kong.

This record separates deployment preparation from a public launch. Harold confirmed that no domain has been purchased and Cloudflare is not signed in. No Cloudflare account configuration, public preview, production deployment, or hosted acceptance result is established by the preparation changes. The earlier [local verification](VERIFICATION.md) keeps its original scope and dates.

## Preparation evidence

The implementation adds locked Wrangler tooling, static asset configuration, production and preview release checks, GitHub verification, hosted HTTP and browser checks, and three-run mobile Lighthouse reporting. See [the deployment runbook](DEPLOYMENT.md) for commands and account settings.

The implementation was submitted in [pull request #1](https://github.com/ngyathin16/harold-ng-portfolio/pull/1). [GitHub Actions run 43802316049](https://github.com/ngyathin16/harold-ng-portfolio/actions/runs/43802316049) passed for commit [`bf4a5bc1e2a66ada14e1ab7a317a6b1fb1d41e26`](https://github.com/ngyathin16/harold-ng-portfolio/commit/bf4a5bc1e2a66ada14e1ab7a317a6b1fb1d41e26). That run checked both preview and production-style builds, release tests, the browser journey at all three widths, and verification artifacts. The production-style build used the isolated `https://portfolio-ci.invalid` fixture. It was not deployed.

| Check | Observed result |
| --- | --- |
| Fresh `npm ci` in the isolated D: checkout | Passed; 393 packages installed, npm reported zero vulnerabilities. |
| `npm run test:release` | Passed; 18 Node tests. |
| Preview `npm run build:deployment` and `npm run check:release -- --mode preview` | Passed locally. |
| `npx wrangler deploy --dry-run` | Passed locally; 33 static assets. No upload or deployment performed. |
| Browser suite against local Workers | Passed; 12 page/viewport combinations and 28 local links. The final journey checks at every width also passed in the linked GitHub run. |
| Wrangler command/schema checks | Version `4.162.0`, preview command help, and preview configuration schema verified. |
| `npx wrangler whoami` | Reported unauthenticated. No Cloudflare account access established. |
| Hosted HTTP/browser checks and hosted Lighthouse | Not run; no final origin or public preview exists. |

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
| Domain | Harold confirmed none purchased; exact-name quote and choice pending |
| Cloudflare account owner and plan | Not authenticated; pending confirmation in Harold's account |
| Public Preview URL and version | Not deployed |
| Production URL | Not deployed |
| Production Git commit | Not deployed |
| Cloudflare production version and deployment | Not deployed |
| Known-good production version | None recorded |
| DNS and TLS test locations | Not tested |

## Launch acceptance

| Requirement | Status | Evidence required to complete |
| --- | --- | --- |
| D1: Ownership and budget | Pending | Domain spelling, owner, registrar, expiration, actual one-year HKD total, current renewal quote, and free hosting plan. Keep receipts and payment details private. |
| D2: Public access | Pending | Clean-browser result, apex and `www` certificate coverage, and DNS/TLS observations from Hong Kong and one independent network or location. |
| D3: Routes and redirects | Pending | Final-origin HTTP report covering required paths, path/query preservation, redirect counts, real 404, security headers, and caching. |
| D4: Visitor journey | Pending | Hosted browser report at 360, 768, and 1440 px, keyboard checks, contact and source links, actual PDF/ZIP bytes, images, and fonts. |
| D5: Search metadata | Pending | Production custom-origin canonicals/social/sitemap/robots, indexability, preview noindex, and 404 noindex. |
| D6: Update delivery | Partially verified | Required GitHub check passed and main protection was applied/read back. Still needs a blocked failing change, visible production update through the normal workflow, and Cloudflare deployment/version matched to its Git SHA. |
| D7: Recovery | Pending | Known-good production version plus isolated recovery Worker versions A and B, rollback command/result, restored HTML and assets. |
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
