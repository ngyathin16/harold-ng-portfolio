---
title: ReleaseGuard
slug: releaseguard
summary: A release safety workbench that makes the evidence for advancing, holding, or stopping a canary inspectable.
status: Verified local application
period: November 2026
role: Personal project, developed with AI assistance
workType: Personal project
contribution: Product requirements, canary workflow, statistical policy, fault scenarios, and evidence design; implementation and verification developed with Codex assistance.
repository: https://github.com/ngyathin16/releaseguard
cover: /images/releaseguard.webp
coverWidth: 1440
coverHeight: 2261
coverAlt: ReleaseGuard showing a healthy candidate eligible to advance, staged traffic allocation, and error and slow-request comparisons for desktop and mobile cohorts.
coverCaption: The running local application after a real HTTP measurement checkpoint. The data is synthetic; the requests and decisions are executed.
order: 2
technologies: [TypeScript, Node.js, React, SQLite]
---

## The problem

A release can look healthy on average while making one group of users worse off. Sparse measurements can also look reassuring when they actually provide too little evidence. Stopping the rollout adds another question: did new traffic really stop reaching the candidate?

ReleaseGuard makes those decisions inspectable in a local experiment. It compares a fresh baseline and candidate, checks each required cohort, and verifies withdrawal through the router and subsequent requests.

## The workflow

Start with a healthy catalogue service or choose a fault such as a mobile-only slowdown, a configuration error, lost telemetry, or a candidate crash. Inspect the policy before starting. The generator sends real HTTP requests to separate stable, baseline, candidate, and router processes.

The exposure stages are 5% baseline / 5% candidate, then 25% / 25%, then 50% / 50%. The stable service handles the remainder. A stage may become eligible, detect a regression, or remain inconclusive. Advancement always requires an explicit action. The final stage completes the local experiment; it does not promote a production release.

## Decisions that matter

- **A pass needs evidence.** The upper bound on degradation must fit within the practical tolerance, the baseline must be healthy, and every required cohort must have enough complete observations.
- **Charts do not authorize extra tests.** The policy freezes the sample checkpoints. Conservative binomial intervals account for both outcomes, cohorts, arms, interval tails, stages and checkpoints. p95 is descriptive context.
- **Missing measurements hold the release.** Lost telemetry cannot become zero errors or zero latency. After the declared grace period, the router withdraws candidate traffic.
- **A stop must be observed.** Generation checks fence stale commands. Lease expiry protects new traffic if the controller disappears. Withdrawal is confirmed through router acknowledgement and version-tagged baseline requests.
- **Earlier evidence stays intact.** SQLite history retains the policy, input prefix, judgment and control events. Exported raw requests can reproduce the exact judgment without reproducing live timing.

## Measured evidence

The frozen synthetic evaluation includes 200 no-regression simulations and 100 prespecified large regressions, plus a separate boundary suite and correlated-workload stress suite. It produced **0/200 false regression flags**, **100/100 large-regression detections**, and **0/100 false advancements**. Of the healthy simulations, **135/200 completed all stages** and **65/200 remained inconclusive**. All 60 near-boundary simulations were inconclusive. These are finite synthetic measurements, not a universal safety guarantee.

The [statistical record](https://github.com/ngyathin16/releaseguard/blob/main/docs/STATISTICS.md) links independent SciPy reference calculations, the frozen manifests, raw observations, uncertainty intervals and initial failed evaluations. The conservative method's delays are reported alongside its useful detections. An overall-threshold baseline is evaluated against the same captured inputs.

The [runtime verification record](https://github.com/ngyathin16/releaseguard/blob/main/docs/VERIFICATION.md) includes real process tests, repeated live fault scenarios, controller termination during a stop, stale commands, and observed withdrawal timings. The browser workflow was checked at 360, 768, and 1440 pixels, including keyboard operation and automated accessibility checks. Automated testing is not a human usability study.

## Watch and reproduce

<video controls preload="none" poster="/images/releaseguard.webp" width="1440" height="1000" aria-label="Captioned recording of the ReleaseGuard local workflow">
  <source src="/downloads/releaseguard-walkthrough.webm" type="video/webm" />
  <track kind="captions" src="/downloads/releaseguard-walkthrough.vtt" srclang="en" label="English" default />
  <a href="/downloads/releaseguard-walkthrough.webm">Download the captioned walkthrough.</a>
</video>

This 79.72-second recording shows the real local workflow captured on 17 November 2026. It is a recording, not a live hosted application. Captions are visible in the video and available as a separate track.

```sh
git clone https://github.com/ngyathin16/releaseguard.git
cd releaseguard
npm ci
npm run build
npm start
```

Use Node 24.14 or later in the 24.x line, then open `http://127.0.0.1:4318`. No API key is needed. Verification and evidence-reanalysis commands are in the [README](https://github.com/ngyathin16/releaseguard).

## Scope and limits

The project routes only to its own local services and uses synthetic data. The traffic shares one host; it does not recreate production resource isolation or independent user behavior. The statistical assumptions and conservative inconclusive outcomes remain visible. Rollback moves new requests to the baseline; requests already in flight can finish or time out. There is no external chaos testing, full production promotion, hosted backend, or human participant study.

The problem framing draws on [Netflix's Kayenta canary analysis](https://netflixtechblog.com/automated-canary-analysis-at-netflix-with-kayenta-3260bc7acc69), [Netflix's discussion of repeated testing](https://netflixtechblog.com/sequential-a-b-testing-keeps-the-world-streaming-netflix-part-1-continuous-data-cba6c7ed49df), and [Meta's configuration-canary episode](https://engineering.fb.com/2026/04/08/security/trust-but-canary-configuration-safety-at-scale-meta-tech-podcast/). ReleaseGuard does not reproduce their platforms or imply affiliation or endorsement.
