# Harold Ng — portfolio

Harold Ng's portfolio as an AI Solutions Engineer: static Astro pages with Markdown case studies for QueryProof, ReleaseGuard, Relay Desk and IFC-GPT. Node.js 24 is required.

Live site: **[haroldng.dev](https://haroldng.dev)**. Hosted on Cloudflare Workers Free; see the [launch evidence](docs/DEPLOYMENT-VERIFICATION.md).

The latest completed projects are [QueryProof](https://haroldng.dev/work/queryproof/), a SQL metric review workbench, and [ReleaseGuard](https://haroldng.dev/work/releaseguard/), a local canary release workbench. Their case studies link public source, recorded walkthroughs, measured evidence and local startup instructions. Both also appear in the downloadable résumé.

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

The build validates case-study metadata and produces `dist/`. With the preview running at `http://127.0.0.1:4321`, run `npm test` for browser and accessibility checks, then `npm run test:design` for responsive navigation, current title metadata, workflow diagrams, native mobile contents, 200% text reflow and no-JavaScript journeys. Run `npm run audit` for three mobile Lighthouse reports. Browser verification uses Microsoft Edge on Windows; install Playwright Chromium with `npx playwright install chromium` on other platforms. A custom browser path can be supplied through `BROWSER_EXECUTABLE`. The design suite also supports `BROWSER_ENGINE=firefox` or `webkit` after installing those Playwright browsers.

The design uses adapted, licensed author code for the 21st.dev-selected navigation, project cards and timeline. [Component adoption](docs/COMPONENT-ADOPTION.md) records the exact sources, commits, licenses, adaptations and runtime cost.

See [verification results](docs/VERIFICATION.md), [change history](docs/CHANGELOG.md), the [deployment PRD](docs/DEPLOYMENT-PRD.md), and [deployment instructions](docs/DEPLOYMENT.md).

## Edit a case study

Edit `content/work/*.md`. Astro loads the directory using a schema in `src/content.config.ts`. The filename is an internal content ID; `slug` sets the public `/work/<slug>/` route. `order` controls homepage order.

Required metadata:

```yaml
title: Project name
slug: project-name
summary: One plain sentence describing the project.
focus: The central problem or observation.
decision: The important engineering decision and why it matters.
evidence:
  label: Read the verification evidence
  url: https://github.com/owner/repository/blob/main/docs/VERIFICATION.md
workflow:
  - title: Gather the input
    description: Describe what enters the system.
  - title: Check the evidence
    description: Explain the substantive processing step.
  - title: Review the result
    description: Explain what the person can decide or inspect.
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

Supply at least one of `repository` (HTTPS URL), `sourceDownload` (`/downloads/...`), or `demo` (HTTPS URL). The `workflow` needs at least three steps. Optional `walkthrough` links a recording; it does not imply a hosted application. A build fails when required metadata is missing. Use `##` headings for the problem, approach, decisions, evidence, and limits; the page generates its table of contents from these headings. Use absolute local paths in Markdown links and images, such as `/images/relay-evaluation.webp`, so they also work on nested routes. Give body images explicit dimensions and alt text. Link to a full screenshot when the page displays a cropped preview.

Images belong in `public/images`. Real product screenshots accompany each case study. Source provenance and capture details are recorded in [IFC verification](docs/IFC-VERIFICATION.md) and [Relay verification](https://github.com/ngyathin16/relay-desk/blob/main/docs/VERIFICATION.md). Do not add unsupported result claims or private employer material.

## Résumé and generated assets

- Edit `content/resume.md`, then run `npm run resume`. This generates `public/resume.pdf` using Edge on Windows, or the Chromium-based browser in `BROWSER_EXECUTABLE`.
- Run `node scripts/build-social.mjs` to regenerate the 1200 × 630 social image.
- Run `python scripts/package-relay.py --source ../relay-desk` after updating a sibling Relay Desk checkout. It creates `public/downloads/relay-desk.zip` from an explicit allowlist, excluding credentials and local databases. The zip has its own startup instructions. Keep the archive and published application source in sync. A source archive already ships with this repository; cloning Relay is only needed to regenerate it.
- Newsreader and DM Sans are self-hosted from their Fontsource packages; licenses ship in `public/fonts/`. Only the navigation hydrates as a React island. Project cards render to static HTML; the timeline and case-study contents are static Astro/native HTML. All content and links remain usable with JavaScript disabled. Cloudflare injects its analytics beacon into the hosted page; hosted measurements include that script.

## Supporting project

```sh
git clone https://github.com/ngyathin16/relay-desk.git
cd relay-desk
npm start
```

Open `http://127.0.0.1:3210`. The free baseline requires no package install or API credential. [Relay Desk’s README](https://github.com/ngyathin16/relay-desk) covers tests, evaluation, optional live models, and limitations. This is a local, single-user service with a mock CRM; static hosting does not run it. The public repository can be browsed without signing in; the portfolio also includes a reviewed standalone source ZIP.

Maintain Relay Desk in its own repository. Keep application changes, documentation, and verification together there, then update this portfolio’s case study and source archive to match the published revision. This portfolio repository contains the site and evidence assets; it does not include the application’s working database or credentials.

## Launch configuration

Cloudflare Workers Static Assets publishes only `dist/` at `https://haroldng.dev`. Wrangler is locked in the npm dependencies. Hosting uses the free plan. The domain cost US$8.20 for the first year; the quoted annual renewal is US$12.20, subject to the HK$200 yearly budget and a price review before renewal. See the [deployment evidence](docs/DEPLOYMENT-VERIFICATION.md).

Set build-time `SITE_URL` and independent `APPROVED_SITE_URL` to the selected custom HTTPS origin for production. Set `PUBLIC_RESEARCH_API_URL` to the qualified public HTTPS API origin; the production check requires the research page's emitted destination to match exactly and rejects missing, local or insecure values. `npm run build:deployment` runs the Astro build and release checks. Workers Builds selects the mode from its branch. For a local run, set `DEPLOYMENT_MODE` to `production` or `preview`. Preview builds require `SITE_URL` to be absent and emit noindex. The production site-origin check rejects missing, mismatched, placeholder, and provider origins.

`npm run test:release` tests the release checks. `npm run check:release -- --mode production` checks a built production artifact. GitHub's `release-checks` job validates preview and production-style builds and runs the browser suite before merging to protected `main`. Workers Builds deploys verified `main` commits automatically. Follow the [deployment runbook](docs/DEPLOYMENT.md) for settings, release verification, and recovery.

After deployment, set `BASE_URL` to the final origin and run `npm run verify:hosted -- --mode production`, `npm test`, `npm run test:design`, and `npm run audit`. Hosted production checks also require `APPROVED_SITE_URL`. Audit runs retain three mobile Lighthouse reports and require median performance/accessibility of at least 95, LCP at most 2.5 seconds and CLS at most 0.1. Supply the actual `GITHUB_SHA` and `DEPLOYMENT_VERSION` for traceable reports. Hosting checks do not run the Relay Desk backend.

## Repository maintenance

Commit and push verified updates to this repository as work is completed. Update case studies, documentation, and the change history alongside behavior or evidence changes. Run the build and affected browser checks before pushing; confirm the remote branch contains the intended commit afterward. Keep private planning files and credentials outside the published repository. Publishing source to GitHub does not deploy the website.

## Research workspace

Applications and the research explorer live at `/applications/` and `/applications/research/`. See [setup and release status](docs/RESEARCH-WORKSPACE.md) and [backend operations](projects/research-workspace/README.md). Set only `PUBLIC_RESEARCH_API_URL` for the browser; the static build requires no research database or secret. General portfolio smoke checks fetch this route as static HTML and do not invoke the research API. Dynamic `npm run test:research` verification remains separate and requires a reviewed request allowance; enabling `TEST_COMPLETED_QUERY=1` also submits a search.
