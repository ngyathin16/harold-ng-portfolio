# Component adoption record

Adopted on 18 November 2026 for Harold Ng's AI Solutions Engineer portfolio. The selected 21st components were traced to public author source and adapted into the site. They were not installed as a library-wide package. Changes to author code are described below; original MIT notices are retained in `public/licenses/` and served with the site.

## 1. Tubelight navigation

| Field | Record |
| --- | --- |
| Catalog | [Tubelight Navbar — Ayushmaan Singh / Serenity UI](https://21st.dev/@ayushmxxn/components/tubelight-navbar) |
| Author revision | `ayushmxxn/serenity-ui@c19872c281f29809eccee60cf9c73daa41828818` |
| Author file | [`app/components/serenity/tubelight-navbar.tsx`](https://github.com/ayushmxxn/serenity-ui/blob/c19872c281f29809eccee60cf9c73daa41828818/app/components/serenity/tubelight-navbar.tsx) |
| Local files | `src/components/TubelightNav.tsx`; navbar rules in `src/styles/components.css` |
| License | MIT, copyright 2026 Ayushmaan Singh; [retained notice](../public/licenses/serenity-ui-MIT.txt) |
| Original dependencies | React, Framer Motion, Next Link, React Icons, Tailwind styling |
| Integrated dependencies | React; browser Web Animations and ResizeObserver APIs; local CSS |
| Integration | `Layout.astro` renders `<TubelightNav pathname={Astro.url.pathname} client:load />` |

Retained implementation: mapped navigation items with selected state, rounded pill grouping, inset active pill, centered lamp and two blurred glow layers. The original glow dimensions, blur/opacity treatment, border, radius and subtle shadow are carried into named CSS rules and warm site tokens.

Adaptations: Next Link becomes native anchors; all four links retain visible text on mobile instead of hiding names behind icons, and wrap when enlarged text needs more room. The selected indicator is shared across items and moves through a small native animation instead of Motion layout IDs. It follows the visible homepage section, including the short contact section at the document end, honors reduced motion, recomputes after resizing and cleans up its observers/listeners. A header ResizeObserver updates the anchor offset when its height changes. Navigation has an accessible name, visible keyboard focus and `aria-current`. Initial HTML contains the links and a CSS active treatment; failed or disabled JavaScript does not hide navigation. React Icons and Next.js are not required.

Variant difference: the current author's file exports `TubeLightNavbar` with hardcoded tabs and React Icons. The older catalog usage exports `NavBar` accepting items and uses Lucide. This integration derives from the pinned author file, not an uninspected gated catalog payload.

## 2. Project showcases

| Field | Record |
| --- | --- |
| Catalog | [Features 8 — Méschac Irung / Tailark](https://21st.dev/@meschacirung/components/features-8) |
| Author revision | `tailark/blocks@8139698115c1341bfd2e3e286c04bb4d8146f472` |
| Author files | [`registry/bases/base/mist/blocks/features/eight.tsx`](https://github.com/tailark/blocks/blob/8139698115c1341bfd2e3e286c04bb4d8146f472/registry/bases/base/mist/blocks/features/eight.tsx), [`registry/bases/base/mist/ui/card.tsx`](https://github.com/tailark/blocks/blob/8139698115c1341bfd2e3e286c04bb4d8146f472/registry/bases/base/mist/ui/card.tsx) |
| Local files | `src/components/ProjectShowcase.tsx`; showcase rules in `src/styles/global.css` |
| License | MIT, copyright 2025 Irung; [retained notice](../public/licenses/tailark-MIT.txt) |
| Original dependencies | React, Card, Lucide, Next Image; Card uses class-variance-authority and the shared class helper; Tailwind styling |
| Integrated dependencies | React for server rendering; local CSS; ordinary images with source dimensions |

Retained implementation: the responsive feature grid, larger screenshot-led feature treatment, smaller supporting cards and forwarded Card props/ref. The generic Card variants/class composition collapse into the single adopted site treatment; Card becomes a semantic article. The full-width feature pattern is expanded to two featured projects and two supporting projects. Stock AI feature copy/assets are replaced with the site's four verified projects, decisions, statuses, technology lists, source links and screenshot evidence. Image framing and full-image links preserve access to source evidence. This component is rendered on the server without client hydration.

Variant difference: the current public author file is Tailark Mist Features Eight, with one wide screenshot feature and three small cards. The old 21st item is not claimed to be byte-identical. The pinned author implementation supplies the reusable grid/Card foundation; page-specific hierarchy and evidence framing are adaptations. Neither a wholesale Tailwind install nor Next.js is needed for the retained code.

## 3. Experience chronology

| Field | Record |
| --- | --- |
| Catalog | [Timeline — Elia Kuratli / Arc](https://21st.dev/@kuratlielia/components/timeline) |
| Author revision | `kuratlielia/arc-library@8302b52ed9dc79ae7ca699b158a98dfdf09656f7` |
| Author files | [`registry/components/timeline/timeline.tsx`](https://github.com/kuratlielia/arc-library/blob/8302b52ed9dc79ae7ca699b158a98dfdf09656f7/registry/components/timeline/timeline.tsx), [`timeline.module.css`](https://github.com/kuratlielia/arc-library/blob/8302b52ed9dc79ae7ca699b158a98dfdf09656f7/registry/components/timeline/timeline.module.css) |
| Local files | `src/components/ExperienceTimeline.astro`; timeline rules in `src/styles/components.css` |
| License | MIT, copyright 2026 Elia Kuratli; [retained notice](../public/licenses/arc-library-MIT.txt) |
| Original dependencies | React, Motion, Lucide, motion tokens, CSS modules |
| Integrated dependencies | Astro server rendering and source-derived CSS; no client JavaScript |

Retained implementation: grouped headings and ordered rows; marker, connecting segment and content structure; time metadata; separate detail area; minimum-width handling; and the author's 28px marker-column geometry. The connecting segment retains the source's calculations from marker position, size and line gap. These source CSS rules are ported into component-prefixed rules with site tokens, rather than drawing an unrelated timeline from a screenshot.

Adaptations: day/activity groups become Experience and Education, using the existing job/degree facts and Harold's confirmed AI Solutions Engineer title. Native headings replace role-based headings. The continually changing feed clock, live announcements, insert animations, scroll viewport and expandable details are removed because the chronology is static. Details stay expanded; dates use semantic time elements. Compact typography and spacing replace feed styling. The source-preserving Astro port keeps the adopted structure readable without hydration and adds no Motion runtime.

Why this licensed alternative: Aceternity Timeline was shortlisted visually, but rights for its current author payload were not established from the older catalog's MIT label. The approved PRD permits another licensed, source-backed timeline. Arc's pinned repository provides an explicit MIT grant for the files actually adapted here. No Aceternity code was imported.

## Prompt access and sources not incorporated

During the design review, 21st's Copy prompt action opened an account-creation dialog. No supplied prompt or gated component file was retrieved, and no invented prompt is represented as an official one. The actual public author files above were retrieved and read before adaptation; the user-approved PRD governed the changes. Repository and source licenses remain distinct from access to the catalog UI.

React Bits SpotlightCard was evaluated as an optional interaction reference, but no React Bits source was copied into these components and no React Bits component is claimed as installed. FadeContent, SplitText, shader effects and the optional Serenity gallery are not part of this adoption. The public skill additions are a separate deliverable, not evidence of website component integration.

## Verification

The production build passed with zero errors, warnings or hints. The [verification record](VERIFICATION.md) covers integrated navigation, keyboard, motion, text reflow and no-JavaScript journeys. All first-party external chunks plus inline bootstrap scripts total a conservative 72,258 bytes gzip per route; project cards and the timeline add no client chunks. Source adaptation is reviewed alongside the visible implementation in the [design record](DESIGN-UPGRADE.md).
