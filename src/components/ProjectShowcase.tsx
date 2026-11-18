// Adapted from Tailark Mist Features Eight and Card (MIT); see docs/COMPONENT-ADOPTION.md.
import { forwardRef, type HTMLAttributes } from 'react';
import type { CollectionEntry } from 'astro:content';

const Card = forwardRef<HTMLElement, HTMLAttributes<HTMLElement>>(({ className = '', ...props }, ref) => (
  <article ref={ref} className={`showcase-card ${className}`} {...props} />
));
Card.displayName = 'Card';

export default function ProjectShowcase({ projects }: { projects: CollectionEntry<'work'>['data'][] }) {
  return (
    <div className="showcase-grid">
      {projects.map((project, index) => (
        <Card key={project.slug} className={`project-${project.slug} ${index < 2 ? 'showcase-featured' : 'showcase-supporting'}`}>
          <div className="showcase-copy">
            <div className="project-kicker"><span>{String(index + 1).padStart(2, '0')} / {project.focus}</span><span>{project.period}</span></div>
            <h3>{project.title}<span aria-hidden="true">.</span></h3>
            <p className="showcase-summary">{project.summary}</p>
            <p className="showcase-decision"><span>The decision</span>{project.decision}</p>
            <p className="showcase-contribution"><span>My contribution: </span>{project.contribution}</p>
            <ul className="technology-list" aria-label={`${project.title} technologies`}>{project.technologies.slice(0, 4).map(technology => <li key={technology}>{technology}</li>)}</ul>
            <div className="showcase-actions">
              <a className="project-case-link" href={`/work/${project.slug}/`} aria-label={`Read the ${project.title} case study`}>Read case study <span aria-hidden="true">↗</span></a>
              {project.repository && <a className="project-source-link" href={project.repository} aria-label={`${project.title} source repository`}>Source <span aria-hidden="true">↗</span></a>}
            </div>
            <p className="project-status"><span className="status-dot" aria-hidden="true" />{project.status}</p>
          </div>
          <figure className="showcase-figure">
            <div className="showcase-frame">
              <div className="frame-bar" aria-hidden="true"><span className="frame-dots"><i /><i /><i /></span><span>{project.slug} / local application</span><span>↗</span></div>
              <a href={project.cover} className="showcase-image" aria-label={`Open full ${project.title} screenshot`}>
                <img src={project.cover} alt={project.coverAlt} width={project.coverWidth} height={project.coverHeight} loading="lazy" decoding="async" />
                <span className="image-open" aria-hidden="true">View full image ↗</span>
              </a>
            </div>
            <figcaption><span className="evidence-label">Evidence</span><a href={project.evidence.url}>{project.evidence.label}<span aria-hidden="true"> ↗</span></a></figcaption>
          </figure>
        </Card>
      ))}
    </div>
  );
}
