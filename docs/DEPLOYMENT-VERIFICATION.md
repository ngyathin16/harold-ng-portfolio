# Deployment verification

Record date: 16 November 2026, Asia/Hong_Kong.

This record separates deployment preparation from a public launch. No domain purchase, Cloudflare account configuration, public preview, production deployment, or hosted acceptance result is established by the preparation changes. The earlier [local verification](VERIFICATION.md) keeps its original scope and dates.

## Preparation evidence

The implementation adds locked Wrangler tooling, static asset configuration, production and preview release checks, GitHub verification, hosted HTTP and browser checks, and three-run mobile Lighthouse reporting. See [the deployment runbook](DEPLOYMENT.md) for commands and account settings.

Clean `npm ci` passed in the isolated checkout with 393 packages installed and no reported vulnerabilities. Additional local execution results and remote repository configuration must be added after verification. A committed workflow does not itself prove that GitHub branch protection is active. A Wrangler dry run does not prove account access, a deployed preview, DNS, or TLS.

The initial dependency installation encountered insufficient space on the C: drive. Implementation and clean-install verification moved to an isolated checkout on D:. This storage workaround does not establish a hosted result.

| Field | Recorded value |
| --- | --- |
| Repository | `ngyathin16/harold-ng-portfolio` |
| Preparation base commit | `e1f0df7` |
| Prepared Worker name | `harold-ng-portfolio` |
| Wrangler dependency | `4.162.0`, exact version in the npm lockfile |
| Compatibility date | `2026-11-16` |
| Domain | Pending exact-name quote and Harold's choice |
| Cloudflare account owner and plan | Pending confirmation in Harold's account |
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
| D6: Update delivery | Pending | Active main-branch rule, successful required status, blocked failing change, visible update through the normal workflow, Git SHA, and Cloudflare deployment/version. |
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
