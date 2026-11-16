# Harold Ng — portfolio

A static Astro portfolio with Markdown case studies and an independently runnable Relay Desk project. Node.js 24 is required.

```sh
npm ci
npm run dev
```

Open `http://localhost:4321`. On Windows, use `npm.cmd` if PowerShell blocks `npm.ps1`.

## Build and verify

```sh
npm run build
npm run preview
```

The build validates case-study metadata and produces `dist/`. With the preview running at `http://127.0.0.1:4321`, run `npm test` for browser checks. Run `node scripts/audit-site.mjs` for the mobile Lighthouse report. Browser verification uses Microsoft Edge on Windows; install Playwright Chromium with `npx playwright install chromium` on other platforms. A custom browser path can be supplied through `BROWSER_EXECUTABLE`.

See [verification results](docs/VERIFICATION.md), [change history](docs/CHANGELOG.md), the [deployment PRD](docs/DEPLOYMENT-PRD.md), and [deployment instructions](docs/DEPLOYMENT.md).

## Edit a case study

Edit `content/work/*.md`. Astro loads the directory using a schema in `src/content.config.ts`. The filename is an internal content ID; `slug` sets the public `/work/<slug>/` route. `order` controls homepage order.

Required metadata:

```yaml
title: Project name
slug: project-name
summary: One plain sentence describing the project.
period: 'November 2026'
workType: Personal project
role: Your role
contribution: Your specific contribution and any collaboration or assistance.
status: Local working prototype
technologies: [JavaScript, Node.js]
repository: https://github.com/owner/repository
cover: /images/project-name.webp
coverWidth: 1440
coverHeight: 900
coverAlt: A description of the actual screenshot.
coverCaption: What is running, how it was captured, and any relevant limits.
order: 3
```

Supply at least one of `repository` (HTTPS URL), `sourceDownload` (`/downloads/...`), or `demo` (HTTPS URL). A build fails when required metadata is missing. Use `##` headings for the problem, approach, decisions, evidence, and limits; the page generates its table of contents from these headings. Use absolute local paths in Markdown links and images, such as `/images/relay-evaluation.webp`, so they also work on nested routes. Give body images explicit dimensions and alt text. Link to a full screenshot when the page displays a cropped preview.

Images belong in `public/images`. Real product screenshots accompany each case study. Source provenance and capture details are recorded in [IFC verification](docs/IFC-VERIFICATION.md) and [Relay verification](https://github.com/ngyathin16/relay-desk/blob/main/docs/VERIFICATION.md). Do not add unsupported result claims or private employer material.

## Résumé and generated assets

- Edit `content/resume.md`, then run `npm run resume`. This generates `public/resume.pdf` using Edge on Windows, or the Chromium-based browser in `BROWSER_EXECUTABLE`.
- Run `node scripts/build-social.mjs` to regenerate the 1200 × 630 social image.
- Run `python scripts/package-relay.py --source ../relay-desk` after updating a sibling Relay Desk checkout. It creates `public/downloads/relay-desk.zip` from an explicit allowlist, excluding credentials and local databases. The zip has its own startup instructions. Keep the archive and published application source in sync. A source archive already ships with this repository; cloning Relay is only needed to regenerate it.
- Newsreader and DM Sans are self-hosted from their Fontsource packages; licenses ship in `public/fonts/`. The portfolio ships no browser JavaScript.

## Supporting project

```sh
git clone https://github.com/ngyathin16/relay-desk.git
cd relay-desk
npm start
```

Open `http://127.0.0.1:3210`. The free baseline requires no package install or API credential. [Relay Desk’s README](https://github.com/ngyathin16/relay-desk) covers tests, evaluation, optional live models, and limitations. This is a local, single-user service with a mock CRM; static hosting does not run it. The public repository can be browsed without signing in; the portfolio also includes a reviewed standalone source ZIP.

Maintain Relay Desk in its own repository. Keep application changes, documentation, and verification together there, then update this portfolio’s case study and source archive to match the published revision. This portfolio repository contains the site and evidence assets; it does not include the application’s working database or credentials.

## Launch configuration

Cloudflare Workers Static Assets publishes only `dist/`. Wrangler is locked in the npm dependencies. The deployment uses free hosting and one domain costing at most HK$200 annually including renewal. Domain ownership and a live release remain subject to the [deployment evidence](docs/DEPLOYMENT-VERIFICATION.md).

Set build-time `SITE_URL` and independent `APPROVED_SITE_URL` to the selected custom HTTPS origin for production. `npm run build:deployment` runs the Astro build and release checks. Workers Builds selects the mode from its branch. For a local run, set `DEPLOYMENT_MODE` to `production` or `preview`. Preview builds require `SITE_URL` to be absent and emit noindex. The production check rejects missing, mismatched, placeholder, and provider origins.

`npm run test:release` tests the release checks. `npm run check:release -- --mode production` checks a built production artifact. GitHub's `release-checks` job validates preview and production-style builds and runs the browser suite before merging to protected `main`. Follow the [deployment runbook](docs/DEPLOYMENT.md) to activate branch protection, connect Workers Builds, purchase the approved domain, configure redirects, and verify recovery.

After deployment, set `BASE_URL` to the final origin and run `npm run verify:hosted -- --mode production`, `npm test`, and `npm run audit`. Hosted production checks also require `APPROVED_SITE_URL`. Audit runs retain three mobile Lighthouse reports and check their median against the 90/90 target. Supply the actual `GITHUB_SHA` and `DEPLOYMENT_VERSION` for traceable reports. Hosting checks do not run the Relay Desk backend.

## Repository maintenance

Commit and push verified updates to this repository as work is completed. Update case studies, documentation, and the change history alongside behavior or evidence changes. Run the build and affected browser checks before pushing; confirm the remote branch contains the intended commit afterward. Keep private planning files and credentials outside the published repository. Publishing source to GitHub does not deploy the website.
