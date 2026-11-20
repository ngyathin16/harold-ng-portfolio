import { useEffect, useLayoutEffect, useRef, useState } from 'react';

// Adapted from Serenity UI's MIT-licensed Tubelight Navbar; see docs/COMPONENT-ADOPTION.md.
type Section = 'work' | 'about' | 'contact' | 'applications';

const tabs: { name: string; section: Section }[] = [
  { name: 'Work', section: 'work' },
  { name: 'Applications', section: 'applications' },
  { name: 'About', section: 'about' },
  { name: 'Contact', section: 'contact' },
];

export default function TubelightNav({ pathname }: { pathname: string }) {
  const [activeTab, setActiveTab] = useState<Section | null>(
    pathname.startsWith('/applications/') ? 'applications' : pathname === '/' || pathname.startsWith('/work/') ? 'work' : null,
  );
  const navRef = useRef<HTMLElement>(null);
  const indicatorRef = useRef<HTMLSpanElement>(null);
  const pendingSection = useRef<Section | null>(null);

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
      // Keep the clicked destination selected while native anchor scrolling passes other sections.
      if (pendingSection.current && pendingSection.current !== current) return;
      pendingSection.current = null;
      setActiveTab(current);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(updateSection);
    };
    const resumeTracking = () => {
      pendingSection.current = null;
      onScroll();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) resumeTracking();
    };

    updateSection();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    window.addEventListener('scrollend', resumeTracking);
    window.addEventListener('wheel', resumeTracking, { passive: true });
    window.addEventListener('touchstart', resumeTracking, { passive: true });
    window.addEventListener('keydown', onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      window.removeEventListener('scrollend', resumeTracking);
      window.removeEventListener('wheel', resumeTracking);
      window.removeEventListener('touchstart', resumeTracking);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [pathname]);

  useLayoutEffect(() => {
    const nav = navRef.current;
    const indicator = indicatorRef.current;
    const activeLink = nav?.querySelector<HTMLAnchorElement>(`[data-section="${activeTab}"]`);
    if (!nav || !indicator || !activeLink) return;

    const positionIndicator = () => {
      const origin = nav.getBoundingClientRect();
      const target = activeLink.getBoundingClientRect();
      const left = target.left - origin.left - nav.clientLeft;
      const top = target.top - origin.top - nav.clientTop;
      indicator.style.width = `${target.width}px`;
      indicator.style.height = `${target.height}px`;
      indicator.style.transform = `translate(${left}px, ${top}px)`;
      nav.dataset.ready = 'true';
    };

    positionIndicator();
    const observer = new ResizeObserver(positionIndicator);
    observer.observe(nav);
    observer.observe(activeLink);
    return () => {
      observer.disconnect();
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
            href={tab.section === 'applications' ? '/applications/' : `/#${tab.section}`}
            data-section={tab.section}
            data-active={isActive ? 'true' : undefined}
            aria-current={isActive ? 'location' : undefined}
            onClick={event => {
              if (tab.section === 'applications' || pathname !== '/' || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              pendingSection.current = tab.section;
              setActiveTab(tab.section);
            }}
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
