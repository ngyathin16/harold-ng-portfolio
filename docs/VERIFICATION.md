# Portfolio verification

## Completed-project update — 18 November 2026

Refreshed the homepage, completion labels, case-study evidence and résumé against the published QueryProof `fa5760f` and ReleaseGuard `74b7a6f` revisions. Application measurements are retained evidence from those projects; their evaluation suites were not rerun for this content update.

On Windows with Node 24.14.0, the production `npm run build:deployment` passed with zero Astro errors, warnings or hints. All **21 release tests** passed. Browser checks against the local Workers runtime at `http://127.0.0.1:4323` passed **18 page/viewport combinations**, **48 local links**, keyboard navigation, downloads and automated accessibility checks. Desktop and mobile homepage screenshots were visually inspected.

`npm run resume` generated the updated PDF. PyMuPDF confirmed **one A4 page**, extractable text for both projects and all **five expected link annotations**. The rendered page was visually inspected for clipping and readability. Local artifacts are `artifacts/portfolio/` and `artifacts/resume-updated.png`; hosted results are recorded on the release pull request after deployment.

## QueryProof and ReleaseGuard integration — 17 November 2026

The production build includes four case studies and passed with zero errors, warnings or hints. Windows Node 24.14.0 and the local Workers runtime were used for `npm test`: **18 page/viewport combinations** at 360, 768 and 1440 px, **48 local links**, keyboard navigation, downloads and automated WCAG-tagged checks passed. All **21 release/deployment tests** passed. Source and measured application evidence are linked in [the implementation record](PROJECTS-IMPLEMENTATION.md). The ReleaseGuard recording is a static asset, with a separate caption track; no local backend is included in static hosting.

Earlier verification entries below describe the then-current two-project portfolio.

## Project-list readiness — 16 November 2026

Verified the content-derived project count, ordered next-project navigation and sitemap-derived browser route list on Windows with Node 24.14.0. `npm run build` passed with zero errors, warnings or hints. `TEST_BASE_URL=http://127.0.0.1:4322 npm test` passed 12 page/viewport combinations, 29 local links, keyboard/navigation/download checks and automated accessibility scans. The current two published case studies remain the only project routes. QueryProof has not yet earned publication under its acceptance gates.

Verified on 11 November 2026 (Hong Kong) against the static production build served by Astro preview at `http://127.0.0.1:4321`.

## Browser and accessibility checks

`npm test` passed all 12 page/viewport combinations: the homepage, both case studies, and the error page, each at 360, 768, and 1440 pixels wide. The browser was Microsoft Edge 157.0.4341.46 on Windows, driven by Playwright 1.65.0.

- No horizontal overflow at any tested width.
- Zero axe-core 4.14.0 findings under the WCAG 2 A/AA, 2.1 AA, and 2.2 AA rule tags.
- All 29 collected local links and fragment targets resolved. Screenshot images decoded successfully and include alternative text and reserved dimensions.
- Keyboard navigation exposes a visible skip link and focus indicator; activating it focuses the main content. Navigation from the homepage to selected work and a case study works.
- The source ZIP downloads with the expected filename and ZIP signature. The résumé response is an actual PDF. Email and LinkedIn targets exactly match the supplied contact details.
- Titles, descriptions, canonical links, sitemap, robots file, and the PNG social preview are present. An unknown path returns HTTP 404 with a useful link home.
- A separate release-configuration build with `SITE_URL=https://portfolio.example.invalid` verified configured canonical/social URLs, all three sitemap routes, indexing rules, and `noindex` on the error page. This reserved example domain was only test input; it is not a deployment or a selected domain.
- No browser console errors or page exceptions on these journeys.

The initial pass identified insufficient contrast in the PDF badge and shell-command syntax colors. Those were corrected before the final passing run. Automated accessibility scans and keyboard checks are evidence toward the accessibility target, not certification or a replacement for assistive-technology review.

The résumé was also independently checked: one readable page, 2,177 extractable text characters, and correct email, LinkedIn, and GitHub link annotations. The downloadable Relay Desk archive was extracted and exercised independently: 9/9 behavior tests and 36/36 exact status/source-ID checks passed, and its default server started without a credential. These fixture checks do not measure real-world or live-model accuracy.

Local evidence: `artifacts/portfolio/browser-check.json` and screenshots named by route and width in `artifacts/portfolio/`. Source-project evidence is recorded separately in [Relay Desk verification](https://github.com/ngyathin16/relay-desk/blob/main/docs/VERIFICATION.md) and [IFC verification](IFC-VERIFICATION.md).

## Mobile Lighthouse

The production homepage met the requested performance and accessibility targets of 90 in all five runs. This checks the shipped local build; it is not a before/after speed claim or a measurement of a public deployment.

| Run | Performance | Accessibility | FCP | LCP | TBT | CLS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 100 | 100 | 753 ms | 753 ms | 0 ms | 0.0047 |
| 2 | 100 | 100 | 753 ms | 753 ms | 0 ms | 0.0047 |
| 3 | 99 | 100 | 753 ms | 2,030 ms | 0 ms | 0.0047 |
| 4 | 100 | 100 | 754 ms | 754 ms | 0 ms | 0.0047 |
| 5 | 100 | 100 | 754 ms | 754 ms | 0 ms | 0.0047 |

Median performance: **100**, range **99–100**. Accessibility: **100** throughout. The median LCP was **754 ms**, with a **753–2,030 ms** range; the spread is retained rather than treating the fastest run as the result.

Conditions:

- Lighthouse 13.6.0, Edge/Chromium 157, Node.js 24.14.0, Windows, Intel Core Ultra 9 285K, 24 logical processors. The system snapshot before testing showed 16% CPU load; other local processes were present.
- Static production output, with a local server and no public-network round trip. Lighthouse simulated mobile throttling: 150 ms RTT, 1,638.4 Kbps throughput, and a 4× CPU slowdown.
- A 412×823 mobile viewport at device scale factor 1.75, standard Lighthouse storage-reset behavior, and five sequential navigation runs.
- Every run transferred a successful document and reported zero failed network requests and zero Lighthouse runtime errors.
- The third run identifies the hero heading as the LCP element and a critical network chain through the stylesheet to the self-hosted fonts. Render-blocking CSS is an estimated 140 ms opportunity in that report. Font/render scheduling is a plausible contributor to the LCP spread; no isolated causal benchmark was performed. There is no measured JavaScript blocking time. These observations bound the local result; they do not establish performance over a hosted network.

The Lighthouse runs preceded a final case-study code-block typography adjustment. That change affected code-block selectors, with no homepage layout or content change. The browser suite was repeated after the final build.

Full JSON and HTML reports are under `artifacts/lighthouse/mobile-1` through `mobile-5`, with settings and metrics in `artifacts/lighthouse/summary.json`. Those generated artifacts are ignored by Git and can be reproduced with the commands below.

## Reproduce

From the repository root:

```sh
npm ci
npm run build
npm run preview
```

Leave the preview running. In another terminal:

```sh
npm test
node scripts/audit-site.mjs
```

The scripts use `http://127.0.0.1:4321` by default; set `TEST_BASE_URL` to change it. On Windows they use installed Microsoft Edge. On another platform, install Playwright Chromium with `npx playwright install chromium` for browser tests and provide `BROWSER_EXECUTABLE` for the Lighthouse browser if Chrome is not automatically discoverable. That variable also overrides the browser-test executable. `AUDIT_RUNS` changes the number of Lighthouse runs; the default is five, and a score below 90 causes a failing exit status.

## Deployment limits

This verification does not establish domain ownership, public HTTPS, hosted caching behavior, social-platform preview rendering, or availability from Hong Kong or California. Those checks require the selected domain and hosting account. Preview builds remain unindexed until a real `SITE_URL` is configured. No screen-reader session or live-model quality benchmark is claimed.
