import { useEffect, useRef, useState } from 'react';

// Adapted from Serenity UI's MIT-licensed Tubelight Navbar; see docs/COMPONENT-ADOPTION.md.
type Section = 'work' | 'about' | 'contact';

const tabs: { name: string; section: Section }[] = [
  { name: 'Work', section: 'work' },
  { name: 'About', section: 'about' },
  { name: 'Contact', section: 'contact' },
];

export default function TubelightNav({ pathname }: { pathname: string }) {
  const [activeTab, setActiveTab] = useState<Section | null>(
    pathname === '/' || pathname.startsWith('/work/') ? 'work' : null,
  );
  const navRef = useRef<HTMLElement>(null);
  const indicatorRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const header = navRef.current?.closest('header');
    if (!header) return;

    const updateHeaderOffset = () => {
      document.documentElement.style.setProperty('--header-height', `${Math.ceil(header.getBoundingClientRect().height) + 16}px`);
    };
    updateHeaderOffset();
    const observer = new ResizeObserver(updateHeaderOffset);
    observer.observe(header);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty('--header-height');
    };
  }, []);

  useEffect(() => {
    if (pathname !== '/') return;

    let frame = 0;
    const updateSection = () => {
      frame = 0;
      let current: Section = 'work';
      for (const tab of tabs) {
        const section = document.getElementById(tab.section);
        if (section && section.getBoundingClientRect().top <= window.innerHeight * 0.35) {
          current = tab.section;
        }
      }
      if (window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) {
        current = 'contact';
      }
      setActiveTab(current);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(updateSection);
    };

    updateSection();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, [pathname]);

  useEffect(() => {
    const nav = navRef.current;
    const indicator = indicatorRef.current;
    const activeLink = nav?.querySelector<HTMLAnchorElement>(`[data-section="${activeTab}"]`);
    if (!nav || !indicator || !activeLink) return;

    const positionIndicator = () => {
      const previous = indicator.getBoundingClientRect();
      const origin = nav.getBoundingClientRect();
      const target = activeLink.getBoundingClientRect();
      const left = target.left - origin.left - nav.clientLeft;
      const top = target.top - origin.top - nav.clientTop;
      const nextTransform = `translate(${left}px, ${top}px)`;
      const ready = nav.dataset.ready === 'true';

      indicator.getAnimations().forEach(animation => animation.cancel());
      indicator.style.width = `${target.width}px`;
      indicator.style.height = `${target.height}px`;
      indicator.style.transform = nextTransform;
      nav.dataset.ready = 'true';

      if (ready && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        indicator.animate(
          [
            { transform: `translate(${previous.left - origin.left - nav.clientLeft}px, ${previous.top - origin.top - nav.clientTop}px)`, width: `${previous.width}px` },
            { transform: nextTransform, width: `${target.width}px` },
          ],
          { duration: 220, easing: 'cubic-bezier(.22, 1, .36, 1)' },
        );
      }
    };

    positionIndicator();
    const observer = new ResizeObserver(positionIndicator);
    observer.observe(nav);
    observer.observe(activeLink);
    return () => {
      observer.disconnect();
      indicator.getAnimations().forEach(animation => animation.cancel());
    };
  }, [activeTab]);

  return (
    <nav ref={navRef} className="tubelight-nav" aria-label="Main navigation">
      <span ref={indicatorRef} className="tubelight-pill" aria-hidden="true">
        <span className="tubelight-lamp">
          <span className="tubelight-glow tubelight-glow-near" />
          <span className="tubelight-glow tubelight-glow-wide" />
        </span>
      </span>
      {tabs.map(tab => {
        const isActive = activeTab === tab.section;
        return (
          <a
            key={tab.section}
            href={`/#${tab.section}`}
            data-section={tab.section}
            data-active={isActive ? 'true' : undefined}
            aria-current={isActive ? 'location' : undefined}
            onClick={() => setActiveTab(tab.section)}
          >
            <span className="tubelight-label">{tab.name}</span>
          </a>
        );
      })}
      <a className="tubelight-resume" href="/resume.pdf" download>
        <span className="tubelight-label">Résumé <span aria-hidden="true">↗</span></span>
      </a>
    </nav>
  );
}
