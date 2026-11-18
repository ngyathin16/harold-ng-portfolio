import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

const site = new URL(process.env.SITE_URL || 'http://localhost:4321');
if (!['http:', 'https:'].includes(site.protocol) || site.pathname !== '/' || site.search || site.hash || site.username || site.password) {
  throw new Error('SITE_URL must be an http(s) origin without a path, query, or credentials.');
}

export default defineConfig({
  site: site.origin,
  output: 'static',
  integrations: [react()],
  trailingSlash: 'always',
  markdown: { shikiConfig: { theme: 'github-light' } },
});
