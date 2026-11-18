# Change history

## 18 November 2026

- Added Harold's portrait beside the introduction and two personal photos in About, using responsive WebP assets, descriptive alternative text, and lazy loading below the fold.

- Marked QueryProof and ReleaseGuard as completed local applications and introduced them in the homepage copy and search/social descriptions.
- Added QueryProof's acceptance link and ReleaseGuard's final 29-test, 12-run control evidence with its measurement scope.
- Added both projects to the one-page résumé, including public source links, and regenerated the downloadable PDF.
- Verified the production build, 21 release tests, 18 responsive page views and 48 local links. See [verification](VERIFICATION.md) for scope.

## 17 November 2026

- Added accepted QueryProof and ReleaseGuard case studies with real screenshots, measured evidence, runnable source and explicit limitations. Preserved Relay Desk and IFC-GPT routes.
- Added a local captioned ReleaseGuard walkthrough and responsive video presentation. Selected work now lists four projects in the intended order.
- Verified the production build, all 18 responsive route views, 48 local links and 21 deployment checks. See the project implementation record for application verification and source delivery.

## 16 November 2026

- Launched [haroldng.dev](https://haroldng.dev) on Cloudflare Workers Free with HTTPS, permanent HTTP/`www` redirects, custom 404 responses, production metadata, and automatic GitHub deployments behind required checks.
- Confirmed the US$8.20 domain purchase, US$12.20 renewal quote, and auto-renew. Rehearsed version rollback in an isolated Worker and proved a failed build leaves production unchanged. See [deployment evidence](DEPLOYMENT-VERIFICATION.md) for release identifiers, hosted checks, and DNSSEC propagation status.
- Published the footer copyright through the normal release workflow and corrected hosted verification for Cloudflare's native 307 trailing-slash response while retaining permanent host/protocol redirects.

- Prepared Workers Static Assets with locked Wrangler, Node 24, isolated noindex previews, custom-origin checks, a required browser/build workflow, and production deployment restricted to verified merged commits.
- Added hosted route, redirect, metadata, caching, download checks and three-run mobile Lighthouse reporting. Updated the launch and recovery runbook; domain purchase and Cloudflare activation remain pending.

- Made the selected-project count derive from published content and next-project links cycle in the same order.
- Changed browser verification to discover project routes from the production sitemap. Build and all 12 current page/viewport checks passed, including 29 local links and automated accessibility scans.
- QueryProof remains under separate development; no unfinished case study is published by this change.

- Added a researched deployment PRD covering free Workers Static Assets hosting, a custom domain within the confirmed HK$200 annual budget, release verification, and recovery. This is planning; no domain purchase or website deployment occurred.

- Published the portfolio and Relay Desk repositories publicly under `ngyathin16` and linked Relay source from its case study.

- Prepared the Astro portfolio as a standalone GitHub repository with public-facing source and documentation.
- Separated Relay Desk development into its own repository and retained the independently runnable source download.
- Made source-archive generation accept a standalone Relay Desk checkout.
- Kept private planning documents and local development history outside the publication export.
- Verified a clean standalone export with `npm ci` and `npm run build`: zero errors, warnings, or hints. Browser checks passed 12 page/viewport combinations, 29 local links, keyboard navigation, and downloads at `http://127.0.0.1:4322` on Windows.

The original local browser, accessibility, and performance evidence is dated 11 November in [verification](VERIFICATION.md). The public launch has its own [deployment evidence](DEPLOYMENT-VERIFICATION.md); a GitHub push alone does not establish a deployed website.

## 11 November 2026

- Built the static Astro portfolio, IFC-GPT and Relay Desk case studies, résumé download, and responsive presentation.
- Recorded build, browser, accessibility, source-download, and local performance verification with their scope and limitations.
