import { defineCollection } from 'astro:content';
import { z } from 'astro/zod';
import { glob } from 'astro/loaders';

const evidenceUrl = z.union([
  z.url({ protocol: /^https$/ }),
  z.string().regex(/^\/(?:images|downloads|work)\//),
]);

const work = defineCollection({
  loader: glob({ pattern: '*.md', base: './content/work' }),
  schema: z.object({
    title: z.string().min(1),
    slug: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
    summary: z.string().min(1),
    focus: z.string().min(1),
    decision: z.string().min(1),
    evidence: z.object({ label: z.string().min(1), url: evidenceUrl }),
    walkthrough: evidenceUrl.optional(),
    workflow: z.array(z.object({
      title: z.string().min(1),
      description: z.string().min(1),
    })).min(3),
    period: z.string().min(1),
    workType: z.string().min(1),
    role: z.string().min(1),
    contribution: z.string().min(1),
    status: z.string().min(1),
    technologies: z.array(z.string()).min(1),
    repository: z.url({ protocol: /^https$/ }).optional(),
    sourceDownload: z.string().startsWith('/downloads/').optional(),
    demo: z.url({ protocol: /^https$/ }).optional(),
    cover: z.string().startsWith('/images/'),
    coverWidth: z.number().int().positive(),
    coverHeight: z.number().int().positive(),
    coverAlt: z.string().min(1),
    coverCaption: z.string().min(1),
    order: z.number().int().positive(),
  }).refine((entry) => entry.repository || entry.sourceDownload || entry.demo, {
    message: 'Each project needs a usable repository, downloadable source, or demo.',
  }),
});

export const collections = { work };
