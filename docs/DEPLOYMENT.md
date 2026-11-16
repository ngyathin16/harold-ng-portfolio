# Deploy the portfolio

Use this runbook with the [deployment requirements](DEPLOYMENT-PRD.md) and [deployment evidence](DEPLOYMENT-VERIFICATION.md). The repository prepares a Cloudflare Workers Static Assets deployment. The evidence record distinguishes local preparation from a public launch.

Work in `ngyathin16/harold-ng-portfolio`. The older `harold-ng-portfolio-showcase` project is unrelated. Publish only `dist/`. The Relay Desk server, repository files, and local databases are outside the static release.

## Prepare the account and repository

1. Use Harold's Cloudflare account and select the Workers free plan. Record its owner and plan in the deployment evidence. Do not enable a paid plan or add a server workload.
2. Authenticate locally with `npx wrangler login`, then check `npx wrangler whoami`. Use browser authentication. Keep tokens, recovery codes, registrant details, and payment information outside the repository and chat.
3. Install the Cloudflare GitHub integration for only `ngyathin16/harold-ng-portfolio`. Use Worker name `harold-ng-portfolio`, which must match `wrangler.jsonc`.
4. Keep production automatic deployment disabled until the domain, repository rules, and production variables are ready. Use an isolated Worker Preview for initial checks.

Wrangler is locked to `4.162.0`. The configuration uses compatibility date `2026-11-16`, `./dist`, `404-page`, and `auto-trailing-slash`. The empty `previews` block enables the separate Preview configuration. `preview_urls: true` retains intentional previews. `workers_dev: false` keeps the production provider endpoint disabled from the start. No request handler or Astro server adapter is needed. [Preview configuration](https://developers.cloudflare.com/workers/previews/configuration/)

## Require verification before merging

The `main` rule was activated and read back on 16 November 2026. It requires `release-checks`, an up-to-date branch, and a pull request, applies to administrators, and disables force pushes and deletion. See [the verification record](DEPLOYMENT-VERIFICATION.md) for the successful CI run and remaining launch evidence. The following steps document that setup for maintenance or recovery.

1. Run the repository's GitHub Actions workflow on a branch or pull request so the `release-checks` status exists.
2. Protect `main` with an active GitHub branch rule or ruleset. Require a pull request, the `release-checks` status, and an up-to-date branch before merging. A sole maintainer can require a pull request without requiring another person's approval.
3. Apply the rule to administrators. Leave the bypass list empty. Disable force pushes and branch deletion. Confirm an unverified direct push cannot reach `main`.
4. Save the rule configuration and a blocked merge or push result in the deployment evidence. A workflow file alone does not activate branch protection.
5. Set the repository Actions variable `APPROVED_SITE_URL` after the exact domain has been approved and connected. Use the HTTPS apex origin without a path, query, credentials, or port.

The `release-checks` job checks a noindex preview and a production-style artifact, runs the browser suite, and retains diagnostics on failure. Before domain selection, its local CI fixture is `https://portfolio-ci.invalid`. The release verifier accepts that origin only with `--mode ci`. That fixture must never be used for a provider deployment. Production deploy checks require the independently configured approved custom origin.

## Configure Workers Builds

Set these values under the Worker's build settings:

| Setting | Value |
| --- | --- |
| Repository | `ngyathin16/harold-ng-portfolio` |
| Root directory | Repository root |
| Production branch | `main` |
| Build command | `npm ci && npm run build:deployment` |
| Deploy command | `npm run deploy:production` |
| Preview command | `npm run deploy:preview` |
| Preview builds | Enabled for non-production branches |
| `NODE_VERSION` | `24`, separately in production and previews |
| `SKIP_DEPENDENCY_INSTALL` | `1`, separately in production and previews |
| Production `SITE_URL` | Approved HTTPS apex origin |
| Production `APPROVED_SITE_URL` | The same separately recorded approved origin |
| Preview `SITE_URL` | Absent |

These are **build variables**. Runtime Worker variables cannot change URLs already written into HTML. Inspect both production and preview settings. Do not copy production `SITE_URL` into previews.

The build command installs the committed lockfile, then calls `npm run build` through the deployment build script. `WORKERS_CI_BRANCH` selects production for `main` and preview for other branches. For local checks, set `DEPLOYMENT_MODE` explicitly. The deploy commands check the artifact before invoking the locked `wrangler deploy` or `wrangler preview`. [Build configuration](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/), [build branches](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/)

The production command also requires a clean `main` checkout associated with a merged pull request. It waits up to ten minutes for GitHub Actions `release-checks` to pass for the exact `WORKERS_CI_COMMIT_SHA`, then checks that the commit is still current `main`. A direct push, failed check, unavailable GitHub API, or timeout stops deployment. Retry a timed-out build after CI passes. Do not replace this command with bare Wrangler in Workers Builds.

Check the first hosted log for Node 24, the npm version, a successful `npm ci`, and Wrangler `4.162.0`. Record the resolved versions. The explicit install replaces automatic dependency installation. [Build image settings](https://developers.cloudflare.com/workers/ci-cd/builds/build-image/)

## Verify the initial preview

Run these PowerShell commands on the implementation branch:

```powershell
npm ci
npm run test:release
Remove-Item Env:SITE_URL -ErrorAction SilentlyContinue
$env:DEPLOYMENT_MODE = 'preview'
npm run build:deployment
npm run check:release -- --mode preview
npx wrangler deploy --dry-run
npm run deploy:preview
```

Use the URL reported by Wrangler as `BASE_URL`. Check it without an authenticated browser session:

```powershell
$env:BASE_URL = Read-Host 'Worker Preview HTTPS origin'
npm run verify:hosted -- --mode preview
npm test
```

Confirm rendered noindex metadata and the provider's preview noindex response. The localhost canonical fallback is expected only in preview artifacts. Noindex does not protect private information. Capture the Preview URL and version in the deployment record.

## Choose and register one domain

Check exact-name quotes for `harold.work`, followed by `haroldng.dev`, `haroldng.work`, and `haroldng.com` if needed. Treat availability and prices in earlier research as historical information.

Before purchase, give Harold the exact spelling, registrar, availability, premium status, one-year registration total, current annual renewal, and expected HKD charges including fees and tax. Both first year and renewal must fit HK$200. Obtain his choice on that concrete quote before purchase. Exclude premium names, add-ons, extra domains, and multi-year commitments.

Register in Harold's account. Enable auto-renew and confirm the payment method and contact email. Record the expiration date, purchase total, and renewal quote without publishing receipts or personal registrant data. Schedule a price review at least 45 days before expiry. Resolve a renewal above the budget before billing; do not silently increase spending or disable renewal.

Cloudflare Registrar requires Cloudflare nameservers. If using another registrar, retain its recovery and DNS instructions. [Registrar nameserver requirement](https://developers.cloudflare.com/registrar/faq/#can-i-use-my-own-third-party-nameservers)

## Connect DNS, HTTPS, and redirects

1. Add the owned domain's zone to Harold's Cloudflare account. For an existing domain, export its DNS records first and preserve mail records. If old DNSSEC is enabled, remove the old DS record and confirm that removal has propagated before changing nameservers.
2. Wait for the zone to become active. Enable Cloudflare DNSSEC and install its DS record at an external registrar when needed. Confirm validated resolution. [DNSSEC instructions](https://developers.cloudflare.com/dns/dnssec/)
3. Add the apex as a Worker Custom Domain. Record it in `wrangler.jsonc` as `routes: [{ "pattern": "<approved-apex>", "custom_domain": true }]`, replacing the placeholder with the owned name. Cloudflare creates the associated DNS record and certificate. Wait for active certificate status. [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
4. For `www`, create a proxied DNS `A` record to `192.0.2.1` for the redirect. Wait for the zone's certificate to cover `www` before testing HTTPS. Do not replace the apex Custom Domain record with this redirect-only record.
5. Create one Single Redirect rule in the zone. Substitute the selected apex in both expressions below. Use a dynamic target, status `301`, and enable **Preserve query string**.

Match expression:

```text
(http.host eq "www.<approved-apex>" or (http.host eq "<approved-apex>" and not ssl))
```

Target expression:

```text
concat("https://<approved-apex>", http.request.uri.path)
```

This sends HTTP apex requests and both `www` schemes directly to HTTPS apex with the original path and query. Check for earlier rules that add another redirect. Expect one redirect for an already canonical path, and at most two when the trailing slash also changes. [Cloudflare redirect procedure](https://developers.cloudflare.com/fundamentals/manage-domains/redirect-domain/)

Keep `workers_dev: false` and verify its actual public behavior after domain checks. Do not describe that provider hostname as a redirect unless a request proves it. Keep `preview_urls: true` for intentional previews. A `.dev` name needs valid HTTPS from the first browser visit. [Provider endpoint configuration](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/), [.dev HTTPS requirement](https://www.registry.google/domains/dev/)

## Release and record the public result

1. Configure the approved origin in both production build variables and the GitHub Actions variable. Run the release checks through a pull request. Enable production automatic deployment only after the branch rule is active.
2. Before each update, save `npx wrangler deployments list` and the last known-good version. Merge the verified pull request. Workers Builds must finish the build and deploy commands successfully.
3. Match the Git commit in the build log to the Cloudflare version and deployment. Set `GITHUB_SHA` and `DEPLOYMENT_VERSION` to those actual values when running the checks below. A successful GitHub push is not a confirmed website update.
4. Run the hosted checks against the final HTTPS apex:

```powershell
$env:BASE_URL = Read-Host 'Approved production HTTPS origin'
$env:APPROVED_SITE_URL = $env:BASE_URL
$env:GITHUB_SHA = Read-Host 'Deployed Git commit SHA'
$env:DEPLOYMENT_VERSION = Read-Host 'Cloudflare version ID'
$env:TEST_LOCATION = Read-Host 'Actual test network and location'
npm run verify:hosted -- --mode production
npm test
$env:AUDIT_RUNS = '3'
npm run audit
```

Retain `artifacts/hosted/http-check.json`, `artifacts/portfolio/`, and all three `artifacts/lighthouse/` reports. Record the Lighthouse test location, browser, mobile simulation conditions, individual scores, and median. The target is median performance and accessibility of at least 90. Automated reports do not establish a Hong Kong or independent-network test by themselves.

5. Open the production origin in a clean browser at 360, 768, and 1440 px. Follow navigation, case studies, email, LinkedIn, GitHub, resume, and source links. Confirm fonts and images load without mixed content or browser warnings. Record DNS and TLS checks from Hong Kong and a second actual network or location.
6. Publish a small visible change through a fresh verified pull request. Confirm the new commit, deployed version, and visible result. Demonstrate that a failing check blocks a merge and that a failing hosted build leaves the last good version active.
7. Complete D1 through D8 in [the evidence record](DEPLOYMENT-VERIFICATION.md). Record any unresolved release requirement before sharing the site as launched.

The hosted verifier checks required routes, future sitemap routes, downloads, real 404 responses, metadata, redirects, and response headers. Keep the security rules in `public/_headers`. HTML and non-fingerprinted files must revalidate. Only `/_astro/*` receives a year-long immutable policy. The local Astro preview cannot prove Cloudflare applied these headers.

## Rehearse recovery before launch

Use a separate temporary Worker named `harold-ng-portfolio-recovery`, with no production routes or Git integration. Build with `SITE_URL` absent so it remains noindex.

Create an ignored configuration at `artifacts/rollback/wrangler.json` with the recovery Worker name, the recorded compatibility date, `workers_dev: true`, and `preview_urls: false`. Set `assets.directory` to the absolute path of the current `dist` directory. Copy the production `404-page` and `auto-trailing-slash` asset settings. Inspect the name and absence of routes before deployment.

1. Run `npx wrangler deploy --config artifacts/rollback/wrangler.json`. Record version A and its public content and asset hashes.
2. Make a visible change only in the recovery artifact. Deploy with the same explicit configuration and record version B. Confirm that the recovery URL changed.
3. Run `npx wrangler rollback <version-A-id> --config artifacts/rollback/wrangler.json`. Check the confirmation names the recovery Worker.
4. Verify that the restored HTML, CSS, images, PDF, ZIP, and real 404 match version A. Save the version IDs, commands, and results. Remove the temporary Worker through its dashboard after retaining the evidence.

No rollback is considered tested until that rehearsal succeeds. Rebuild the normal artifact after the rehearsal.

## Restore a production release

1. Pause automatic builds in the Worker's build settings and cancel queued builds. Confirm no deployment is still in progress.
2. Inspect `npx wrangler deployments list` and select the recorded known-good version.
3. Run `npx wrangler rollback <known-good-version-id> --name harold-ng-portfolio`. Check the confirmation names the production Worker before proceeding.
4. Run the hosted and browser checks. Verify the affected case study, CSS, images, downloads, redirects, and 404. Record the new deployment ID and restored version ID.
5. Revert or fix the faulty commit through a verified pull request. Resume automatic builds only after the repository produces the intended release.

Rollback restores a Worker version and its static assets. It does not revert Git history or DNS. Restore saved DNS records separately if DNS caused the incident. Existing open tabs can still refer to assets from another version. Harold owns Cloudflare and repository recovery access. [Rollback behavior](https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/), [rollback command](https://developers.cloudflare.com/workers/wrangler/commands/workers/#rollback)

## Maintain the deployment

After every release, run public checks and retain the commit and version. Use the provider's existing build and deployment notifications. Monthly, check contact and download paths, certificates, failed builds, and free-plan usage. Review the domain price, payment method, and contact details at least 45 days before expiry.

After a Relay Desk release, regenerate its source archive from the standalone application checkout and update its case study together. Deploy the portfolio through the same verified pull-request flow. Keep the backend local unless a separate hosting decision authorizes it.

## Use Pages only for a verified Workers blocker

Record the blocker before switching platforms. For Cloudflare Pages, use Node 24, `npm ci`, `npm run build`, and output directory `dist`. Retain the production origin checks, separate noindex previews, required GitHub checks, domain redirects, and launch evidence. Use the Pages custom-domain workflow for DNS and TLS. Do not use a Pages hostname as the production canonical origin. [Astro on Pages](https://developers.cloudflare.com/pages/framework-guides/deploy-an-astro-site/)
