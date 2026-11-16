# PRD: Public portfolio deployment and custom domain

- Date: 16 November 2026 (Asia/Hong_Kong).
- Status: Implementation requirements; implemented at [haroldng.dev](https://haroldng.dev) on 16 November 2026. See the [deployment evidence](DEPLOYMENT-VERIFICATION.md) for measured results and remaining follow-ups. The preparation context below records the original planning state.
- Owner: Harold Ng.
- Source: [ngyathin16/harold-ng-portfolio](https://github.com/ngyathin16/harold-ng-portfolio).

## 1. Outcome and agreed budget

Publish the existing Astro portfolio at one memorable custom domain, with reliable HTTPS, working project evidence and contact links, automatic deployment from GitHub, and a documented recovery path.

Harold confirmed a domain budget of **up to HK$200 per year, including renewal**. Hosting should cost **HK$0 per month** for the present static site. Buy one domain, use a free hosting plan, and avoid recurring add-ons. The budget is a ceiling, not a spending target.

This task delivers the deployment PRD and its research, published in the portfolio repository. Purchasing a domain and activating hosting are subsequent implementation actions. The exact domain and checkout total must be selected before purchase; willingness to pay does not establish ownership or approve an unspecified name.

The deployment serves recruiters, hiring managers, prospective teammates, and people following Harold's professional links. Success means they can open the site, inspect work, download the resume or source archive, and contact Harold without an account.

## 2. Scope and current state

The source repository is public. The site is already a static Astro build with case studies, a resume PDF, self-hosted font assets, social imagery, security headers, a sitemap, robots output, and a custom 404. Local build and browser checks have passed; those checks do not establish a working public deployment. See [verification](VERIFICATION.md).

Required launch paths are `/`, `/work/ifc-gpt/`, `/work/relay-desk/`, `/resume.pdf`, `/downloads/relay-desk.zip`, `/images/social-preview.png`, `/robots.txt`, and `/sitemap.xml`. Future case studies must join the same deployment and route verification workflow.

In scope: hosting configuration, GitHub integration, one domain, DNS, HTTPS, canonical redirects, production/preview separation, deployment checks, rollback, and maintenance documentation.

Out of scope: redesigning the site, deploying Relay Desk's Node/SQLite backend, building QueryProof or ReleaseGuard, custom email hosting, contact-form services, a CMS, paid analytics, and server-side rendering. Existing email and LinkedIn contact links remain sufficient. GitHub source publication and website deployment are separate outcomes.

## 3. Hosting decision

Use **Cloudflare Workers Static Assets on the free plan**, connected to the public GitHub repository. Cloudflare currently recommends Workers for new static deployments; Pages still works, but new platform work focuses on Workers. This is a new hosting setup, so the recommendation supersedes the earlier Pages-first notes in this repository. No Worker request-handler code or Astro server adapter is needed for the static site. [Cloudflare recommendation](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/)

| Option | Decision | Reason |
| --- | --- | --- |
| Workers Static Assets + Workers Builds | Selected | Static hosting, Git integration, custom domains, and a current first-party deployment path. |
| Cloudflare Pages | Documented fallback | Fits the existing static build and remains usable; use only if a concrete Workers blocker makes it preferable. |
| Paid hosting tier or a VM | Excluded from launch | The current application has no server workload that justifies it. |

Static asset requests and storage are currently free. Builds have their own free allowance; the researched limit is 3,000 build minutes per month, one concurrent build, and a 20-minute build timeout. Do not add dynamic services or a paid tier by assumption. Record actual plan selection and check usage after launch. [Static asset billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/), [Build limits](https://developers.cloudflare.com/workers/ci-cd/builds/limits-and-pricing/)

Detailed findings and current source links: [hosting research](research/deployment-hosting.md).

## 4. Domain choice and costs

Keep the earlier preference **`harold.work`**, subject to availability and standard pricing. Compare **`haroldng.dev`**, **`haroldng.work`**, and **`haroldng.com`** if necessary. These are candidates, not available-domain claims. Prioritize a name Harold likes that is easy to say and spell, with a normal renewal cost; do not buy a premium or aftermarket name to preserve the shortlist.

Recommend **Cloudflare Registrar** to keep registration, DNS, and hosting in one account. Porkbun is a reasonable alternative if its exact-name quote or registrar flexibility is preferable. Cloudflare Registrar requires Cloudflare nameservers; this convenience has a portability tradeoff if authoritative DNS later moves elsewhere. [Registrar requirement](https://developers.cloudflare.com/registrar/faq/#can-i-use-my-own-third-party-nameservers)

Published standard prices checked 16 November 2026, in USD:

| Registrar / extension | First-year benchmark | Current yearly renewal |
| --- | ---: | ---: |
| Cloudflare `.work` | $10.20 | $10.20 |
| Cloudflare `.dev` | $8.20 advertised introduction; $12.20 regular | $12.20 |
| Cloudflare `.com` | $11.17 | $11.17 |
| Porkbun `.work` | $2.06 advertised sale | $10.81 |

These are extension benchmarks, not checkout quotes for the candidate names. Promotions may change; the displayed renewal is not a guarantee of next year's price. Final affordability must include conversion to HKD, applicable tax, and payment fees. Check both first-year and renewal totals against HK$200; a cheap introductory year alone is insufficient. [Cloudflare prices](https://pricing.registrar.cloudflare.com/), [Porkbun prices](https://porkbun.com/products/domains)

Before purchase, prepare a concrete choice containing exact spelling, registrar, availability, premium status, registration period, first-year total, current renewal total, and expected HKD charge. Obtain Harold's choice on that quote, then purchase in his account. Do not request payment details or account secrets in chat. No extra domains, privacy upsells, email bundles, or multi-year commitments are included.

Enable auto-renew with a valid payment method and accessible contact email. Record the renewal date and review price at least 45 days before expiry, before automatic billing begins. If renewal would exceed the cap, resolve the budget/name decision before billing; do not silently spend more or disable renewal and risk losing the domain. Future registry price changes cannot be prevented by this PRD. [Cloudflare renewal process](https://developers.cloudflare.com/registrar/account-options/renew-domains/)

Detailed comparison and limits: [domain research](research/deployment-domains.md).

## 5. Build and deployment requirements

The canonical working repository is `ngyathin16/harold-ng-portfolio`. Commit reviewed deployment configuration there and keep the public runbook accurate. Connect only this repository to the Cloudflare GitHub integration; do not replace the older `harold-ng-portfolio-showcase` site.

| Setting | Required behavior |
| --- | --- |
| Production branch | `main`; changes to other branches never replace the live site. |
| Runtime for builds | Node.js 24, set explicitly for production and previews. |
| Dependency installation | Use the committed npm lockfile; validate clean `npm ci` locally and confirm the hosted install uses the intended versions. |
| Build | `npm run build`, which performs Astro checking before generating the site. |
| Published directory | `dist/` only. No source-tree, credential, database, or planning-directory upload. |
| Deployment tool | Add a verified Wrangler version as a locked development dependency during implementation. |
| Production deploy | `npx wrangler deploy`, after the build and required checks succeed. |
| Branch preview | Use the current Workers Builds preview command, documented as `npx wrangler preview`; lock a compatible Wrangler version and verify it in the target account. |

Add `wrangler.jsonc` with the actual Worker name, a recorded compatibility date, and assets pointing at `./dist`. Require `not_found_handling: "404-page"` and `html_handling: "auto-trailing-slash"` to fit the existing generated pages. Enable the supported preview configuration and preview URLs explicitly. Avoid SPA fallback: an unknown URL must return a real 404. [Build branches](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/), [Preview configuration](https://developers.cloudflare.com/workers/previews/configuration/), [HTML handling](https://developers.cloudflare.com/workers/static-assets/routing/advanced/html-handling/)

Configuration and checks in this section are work to implement; this PRD does not claim the existing repository already contains Wrangler or hosted CI configuration.

### Production and preview metadata

Set **build-time** `SITE_URL=https://<chosen-domain>` only for production. It must be the approved HTTPS origin without a path, query, or placeholder. The current app deliberately falls back to localhost and noindex when this value is absent; add a production release check that fails when the variable is missing, wrong, or still a provider/testing origin. A runtime Worker variable cannot repair URLs already generated into HTML.

Keep `SITE_URL` unset in the preview build configuration so the existing app emits noindex, and verify this separation rather than assuming branch settings inherit safely. Preview canonical metadata may use the current localhost fallback; it must never enter the production artifact. If preview metadata is later improved, preserve explicit noindex behavior. Native preview-host noindex headers provide a second layer, not a replacement for checking the rendered result. Noindex is not access control, so previews contain only material safe for public viewing.

Production canonicals, Open Graph URLs, image URLs, sitemap entries, and robots sitemap references must all use the selected custom origin. The 404 page remains noindex. Use an isolated Worker Preview for the initial unindexed deployment while selecting and connecting the domain. Do not bypass the production origin check to publish a bootstrap build on `main`.

### Release gate

Before a change reaches `main`, run the build and relevant browser checks against a production-style local artifact or isolated preview. Configure required GitHub status checks and branch rules so production merges cannot bypass them; publish artifacts/results for failed runs. Automatic Cloudflare deployment on a push is not itself a browser-test gate. Block unverified direct pushes to the production branch in the deployment workflow. A failing build must leave the last successful production version in service.

After a successful production deploy, run public URL checks and record the Git commit plus Cloudflare version/deployment identifier. A successful build or GitHub push alone is not evidence that the live site updated.

## 6. Domain, routing, HTTPS, and caching

- Register the chosen domain under Harold's ownership and add its DNS zone to his Cloudflare account. If registered elsewhere, preserve existing DNS/mail records before changing authoritative nameservers. If old-provider DNSSEC is active, remove its DS records at the registrar and allow that removal to propagate before switching nameservers. Do not disturb unrelated domains.
- Once the Cloudflare zone is active, enable DNSSEC when supported, install the new DS record at the registrar if needed, and confirm resolution. Cloudflare Registrar can manage that step automatically. [DNSSEC setup](https://developers.cloudflare.com/dns/dnssec/)
- Attach the apex domain as the Worker Custom Domain and wait for active certificate status. Cloudflare provisions the related DNS/certificate for a Worker Custom Domain; verify the issued certificate on the actual hostname. [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
- Make `https://<domain>` canonical. Configure `www.<domain>` and HTTP requests to redirect permanently to it, preserving the path and query. Validate TLS on `www` before expecting HTTPS redirects to work. Target one redirect for a request whose path is already canonical and at most two when trailing-slash normalization also applies.
- After custom-domain checks pass, disable the production `workers.dev` endpoint while preserving intentional previews. This avoids an extra publicly indexed production hostname. Record the resulting provider-host behavior rather than claiming it redirects. [workers.dev configuration](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/)
- Preserve the existing `public/_headers` security and fingerprinted-asset caching rules. Verify their actual HTTP responses under Workers; do not assume the local preview exercises hosting rules. HTML and non-fingerprinted files such as the resume and source ZIP must revalidate so future updates are visible. Do not apply a year-long immutable cache policy to every path.

If a `.dev` domain is selected, browsers require working HTTPS from the outset due to the extension's HSTS policy. Do not use an HTTP-only temporary launch. [Google Registry](https://www.registry.google/domains/dev/)

## 7. Launch acceptance

All items below are release requirements, not results already measured:

| ID | Acceptance evidence |
| --- | --- |
| D1: Ownership and budget | Exact domain, account owner, expiration date, registrar, actual purchase total, current renewal quote, and free hosting tier recorded; no required annual domain cost above HK$200. Keep receipts/payment details outside the public repository. |
| D2: Public access | A clean browser without login opens the final HTTPS URL. Verify DNS/TLS from Hong Kong and one independent network/location; record locations actually tested. Certificate covers apex and `www`; no mixed-content or browser warnings. |
| D3: Routes and redirects | All required paths return expected content/status. HTTP and `www` normalize correctly with path/query preserved and no loops. Unknown paths return the useful custom page with HTTP 404. |
| D4: Visitor journey | At 360, 768, and 1440 px, navigation, keyboard focus, case studies, email, LinkedIn, GitHub links, resume PDF, and source ZIP work. Confirm actual PDF/ZIP bytes and successful image/font loading. |
| D5: Search metadata | Production is indexable, canonical/social/sitemap URLs use the custom origin, and no localhost/example/provider hostname remains in production metadata. Preview and 404 responses remain noindex. Search-engine indexing itself is not guaranteed by launch. |
| D6: Update delivery | Publish a small visible change through the normal GitHub workflow; confirm its deployed commit and browser result. A failing check/build cannot replace the current release. |
| D7: Recovery | Record a known-good deployment and rehearse restoration in an isolated test deployment. Verify the version's HTML and assets after rollback. Document the exact recovery commands/settings and who owns access. |
| D8: Quality and cost | Run the browser suite against the final origin and resolve launch-blocking issues. Run three hosted mobile Lighthouse checks, target median performance/accessibility at least 90, and retain individual results/conditions. Confirm hosting remains on the free plan. |

Expand existing route assertions when needed. Record browser findings in `docs/DEPLOYMENT-VERIFICATION.md` with the URL, date, commit, provider deployment/version, commands, outcomes, and unresolved limits. Do not overwrite the older local results as if they measured the hosted site.

## 8. Rollback and maintenance

Record the last known-good deployment before each production update. On a broken release, pause further automatic changes, restore that version, and recheck the homepage, affected case study, CSS/images, downloads, redirects, and 404. Correct or revert the faulty Git commit afterward so the next build does not reintroduce it. Cloudflare supports version rollback, including static assets, but platform rollback does not revert Git history or repair DNS changes. Existing open tabs may also encounter asset-version differences. [Rollback behavior](https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/)

If deployment changes DNS, retain the previous record values separately so DNS can be restored if needed. A code rollback and a DNS rollback are different operations. For initial launch, do not declare rollback tested until a known-good version and an isolated rehearsal exist.

Use existing provider build/deployment notifications and run the public smoke checks after every release. Monthly, check the homepage, contact/download paths, certificate status, build failures, and free-plan usage. Before renewal, recheck the domain price and payment/contact details. These are maintenance responsibilities; this PRD does not create scheduled jobs or claim uptime monitoring is already active.

Any ongoing application work remains in its own repository. After a Relay Desk release, update the portfolio source archive and case-study evidence together, then deploy the portfolio through its normal workflow. Keep documentation and the public GitHub repository current during authorized work sessions.

## 9. Delivery sequence and remaining inputs

| Phase | Deliverable | Exit condition |
| --- | --- | --- |
| 1. Prepare | Locked deployment tooling/config, required verification workflow, production/preview settings, updated runbook | Clean build and isolated preview pass; no paid resource required. |
| 2. Select domain | Exact-name availability and checkout comparison within the agreed budget | Harold selects the exact one-year purchase and sees renewal cost. |
| 3. Connect | Owned domain, DNS, Worker custom domain, valid TLS, canonical redirects | Public domain resolves correctly; production origin is set and rebuilt. |
| 4. Validate and launch | Completed D1–D8 record, final URL in repository/portfolio launch records | All release gates pass; disclose any nonblocking limitations. |
| 5. Maintain | Verified GitHub deployments, recovery instructions, renewal/usage checks | Subsequent updates remain reproducible and within the agreed cost model. |

Remaining inputs are the exact domain selection after a current quote, access to Harold's Cloudflare account, and the purchase/registrant steps only he can complete. Use browser/device authentication or existing credential storage; never paste passwords, tokens, recovery codes, or card details into project files or chat.

Preparation can proceed before the domain purchase. Allow one focused implementation session plus DNS/certificate propagation and external checks; re-estimate when account access and name selection are settled. Do not promise a launch timestamp before those dependencies are known. The final handoff includes the live custom-domain URL, verified release identifier, deployment record, recurring cost summary, and recovery instructions.
