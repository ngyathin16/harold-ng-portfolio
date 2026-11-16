import type { APIRoute } from 'astro';

export const GET: APIRoute = ({ site }) => new Response(
  import.meta.env.SITE_URL
    ? `User-agent: *\nAllow: /\nSitemap: ${new URL('/sitemap.xml', site)}\n`
    : 'User-agent: *\nDisallow: /\n',
  { headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
);
