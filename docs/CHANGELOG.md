# Change history

## 16 November 2026

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

The portfolio’s public web deployment and custom domain remain separate work. The original browser, accessibility, and performance evidence is dated 11 November in [verification](VERIFICATION.md); a GitHub push does not establish a deployed website.

## 11 November 2026

- Built the static Astro portfolio, IFC-GPT and Relay Desk case studies, résumé download, and responsive presentation.
- Recorded build, browser, accessibility, source-download, and local performance verification with their scope and limitations.
