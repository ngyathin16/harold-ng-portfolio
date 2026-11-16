# Deployment hosting research

Checked 2026-11-16, Hong Kong time. Scope: deployment planning for `ngyathin16/harold-ng-portfolio`; no account changes, deployment, or purchase performed.

## Recommended host

Use **Cloudflare Workers Static Assets with Workers Builds** on the Free plan. Cloudflare recommends Workers for new projects; Pages remains operational, but new features and optimization focus on Workers. Static assets need no Worker script. This is a planning recommendation based on current product direction and the repository's static output. [Workers best practices](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/)

Workers Builds connects GitHub repositories and automatically builds on pushes. Prepare a Wrangler configuration before connecting this repository: without one, Cloudflare's automatic framework configuration can open a setup PR. The configured Worker name must match Wrangler's `name`. [Workers Builds](https://developers.cloudflare.com/workers/ci-cd/builds/)

Static asset requests are free and unlimited, and asset storage carries no extra charge. Keep this deployment static; Worker execution has separate pricing. [Asset billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)

| Relevant Free-plan limit | Current allowance |
| --- | --- |
| Workers Builds | 3,000 build minutes/month, one concurrent build, 20-minute timeout |
| Static files | 20,000 files per version; 25 MiB per file |
| Worker Previews | 100 per Worker; 100 deployments per Preview |

Sources: [Build limits](https://developers.cloudflare.com/workers/ci-cd/builds/limits-and-pricing/), [asset limits](https://developers.cloudflare.com/workers/platform/limits/#static-assets), [Preview limits](https://developers.cloudflare.com/workers/previews/). These allowances do not include domain registration or renewal.

## Exact proposed configuration

The repository already requires Node 24, builds through `astro check && astro build`, and sets `output: 'static'`. Keep these settings. [package.json](../../package.json), [Astro configuration](../../astro.config.mjs)

| Setting | Proposed value |
| --- | --- |
| Repository / production branch | `ngyathin16/harold-ng-portfolio` / `main` |
| Root / build command | Repository root / `npm run build` |
| Production deploy command | `npx wrangler deploy` |
| Preview command | `npx wrangler preview` |
| Node build variable | `NODE_VERSION=24` in production and preview settings |
| Production build variable | `SITE_URL=https://<approved-canonical-domain>` |
| Preview build variable | Omit `SITE_URL` |

Build commands run before the production or Preview command. Workers Builds uses the project's Wrangler dependency; pin a tested version and commit the lockfile. Node versions can be selected with `NODE_VERSION`. [Build configuration](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/), [build image](https://developers.cloudflare.com/workers/ci-cd/builds/build-image/)

Illustrative `wrangler.jsonc`, to implement later:

```jsonc
{
  "$schema": "node_modules/wrangler/config-schema.json",
  "name": "harold-ng-portfolio",
  "compatibility_date": "2026-11-16",
  "assets": {
    "directory": "./dist",
    "not_found_handling": "404-page",
    "html_handling": "auto-trailing-slash"
  },
  "preview_urls": true,
  "previews": {}
}
```

`404-page` serves the nearest `404.html` with HTTP 404; directory index pages retain trailing slashes. No `main` entry or SSR adapter is needed. Preserve `dist/404.html` and verify an unknown route on the actual host. [Static site configuration](https://developers.cloudflare.com/workers/static-assets/routing/static-site-generation/), [static-only configuration](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/)

New Worker Previews require Wrangler **4.135.0 or later**, an explicitly present `previews` block, and enabled Preview Builds. Production pushes deploy production; other enabled branches run the Preview command. Older projects may still use the earlier Version URL workflow, so inspect account settings instead of assuming identical behavior. [Previews](https://developers.cloudflare.com/workers/previews/), [Preview configuration](https://developers.cloudflare.com/workers/previews/configuration/), [branch controls](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/)

## Metadata and preview isolation

Workers Builds distinguishes production build variables from `previews_base_config.environment_variables`. New Previews copy the base configuration; changing that base does not update existing Previews unless explicitly propagated. These are build variables: runtime variables alone cannot rewrite generated HTML. Configure and verify both scopes before enabling automatic builds. [Builds API reference](https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/)

Current repository behavior depends on `SITE_URL`: omission produces localhost canonical/social URLs and `noindex`; supplying a value permits indexing. Therefore production must fail release acceptance if the origin is missing, localhost, a placeholder, HTTP, or inconsistent with the selected canonical hostname. Changing the hostname requires rebuilding. Check canonical links, Open Graph URLs, social-image URLs, robots, and sitemap in generated output and over HTTPS. The error page must remain unindexed. [Astro configuration](../../astro.config.mjs), [shared layout](../../src/layouts/Layout.astro), [deployment notes](../DEPLOYMENT.md)

Worker Preview URLs are public by default. Cloudflare automatically adds `X-Robots-Tag: noindex` to `workers.dev` Preview URLs, but not custom Preview domains. Keep the site's own preview noindex behavior, and verify the response header. Use Cloudflare Access only if private previews are needed; indexing controls do not provide access control. [Preview protection](https://developers.cloudflare.com/workers/previews/custom-domains/)

## Domain, redirects, and HTTPS

Workers Custom Domains require an active Cloudflare zone. Attach the chosen apex domain using **Settings > Domains & Routes > Add > Custom Domain**; Cloudflare creates DNS records and certificates. Resolve conflicting CNAME records before attachment. Registration can remain at another registrar that permits nameserver changes. [Workers Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/), [nameserver setup](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)

For an apex canonical domain, redirect `www` permanently to HTTPS apex, preserving path and query. A proxied DNS record is needed for redirect-only `www`; Cloudflare documents `192.0.2.1` as a suitable placeholder address. Restrict matching to the exact `www` hostname. Verify HTTPS on both names before announcing the site. [Subdomain redirects](https://developers.cloudflare.com/fundamentals/manage-domains/manage-subdomains/), [redirect settings](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/settings/)

Once custom-domain checks pass, set `workers_dev: false` in Wrangler to remove the duplicate production hostname. Explicit `preview_urls: true` preserves Preview URLs; disabling production `workers.dev` does not disable them. [workers.dev controls](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/)

Preserve existing mail and verification DNS records when moving nameservers. If DNSSEC is enabled at the old provider, remove the old registrar DS configuration before switching, allow that change to propagate, then enable Cloudflare DNSSEC and install its DS record at the registrar. Cloudflare Registrar handles the DS record automatically and requires Cloudflare nameservers. [Full setup](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/), [DNSSEC](https://developers.cloudflare.com/dns/dnssec/), [Registrar FAQ](https://developers.cloudflare.com/registrar/faq/)

## Recovery and verification

Workers can roll back to one of the 100 most recently published versions, directing 100% of traffic to it. Current documentation supports static-asset rollback, but propagation takes time, and a browser with newer HTML can request assets absent from the restored version. Record the known-good version and commit, verify fresh navigation/assets after rollback, and repair or revert Git so the next push does not restore the fault. Treat that Git step as an operational recommendation. [Rollbacks](https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/), [version skew](https://developers.cloudflare.com/workers/versions-and-deployments/version-skew/)

Release acceptance must exercise public HTTPS, redirects with a nested path/query, all three content routes, PDF/source downloads, social image, metadata, robots/sitemap, unknown-path 404, and an unindexed branch Preview that does not replace production. Test from Hong Kong and California; documentation cannot prove network availability or actual account configuration.

## Pages remains an alternative

Pages still supports GitHub automatic deployments. Use `main`, repository root, `npm run build`, `dist`, and `NODE_VERSION=24`; configure production/preview build variables separately. Free limits include 500 builds/month, one concurrent build, 20-minute timeout, 20,000 files, and 25 MiB per file. Static requests are free and unlimited. [Git integration](https://developers.cloudflare.com/pages/get-started/git-integration/), [build settings](https://developers.cloudflare.com/pages/configuration/build-configuration/), [Node selection](https://developers.cloudflare.com/pages/configuration/build-image/), [environment scopes](https://developers.cloudflare.com/pages/functions/bindings/#environment-variables), [limits](https://developers.cloudflare.com/pages/platform/limits/), [static pricing](https://developers.cloudflare.com/pages/functions/pricing/#static-asset-requests)

An apex Pages domain requires Cloudflare nameservers and dashboard domain association; merely adding a CNAME can fail. Pages' root `404.html` prevents unwanted SPA fallback. Previous successful production deployments are rollback targets; previews are not. [Custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/), [serving behavior](https://developers.cloudflare.com/pages/configuration/serving-pages/), [Pages rollback](https://developers.cloudflare.com/pages/configuration/rollbacks/)

Redirect production `<project>.pages.dev` to the canonical domain with a Bulk Redirect preserving path/query. **Disable Include subdomains** to retain branch previews: Cloudflare's example enables it, while its parameter reference confirms that it matches child hostnames. Test both the production redirect and a Preview URL. [Pages redirect guide](https://developers.cloudflare.com/pages/how-to/redirect-to-custom-domain/), [matching parameters](https://developers.cloudflare.com/rules/url-forwarding/bulk-redirects/reference/parameters/)
