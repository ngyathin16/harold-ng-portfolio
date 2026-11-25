# Result storage redesign

November 25, 2026. The owner explicitly chose to redesign result storage to preserve the full search feature while keeping total monthly spending below US$5. This extends the original serverless PRD's storage implementation scope; it does not authorize a paid plan or an increased budget.

## Design work

- [x] Frame: preserve complete result values, counts, discovery order, all three completed-result sorts, arbitrary supported pagination, query identities, cancellation/fencing, expiry and private recovery.
- [ ] Fan out: two independent designs grounded in the existing worker, API, migrations and recovery code.
- [ ] Cross-judge: independent review where the available models permit it.
- [ ] Pick: compare concrete storage, network, request-count and memory bounds.
- [ ] Graft: combine only compatible improvements into one ownership model.
- [ ] Verify: prototype against all 319,352 retained result rows before modifying production persistence.
- [ ] Implement and qualify: migrate all callers, preserve historical results, run local and hosted fault/recovery checks, and reassess the complete monthly envelope.

The design must pass five criteria: exact observable parity; safe atomic publication and stale-worker fencing; bounded PostgreSQL/R2/runtime usage compatible with the monthly limit; independently recoverable private backups and correct retention; and a small, maintainable read/write interface. Estimates do not count as measured acceptance.

Two designs will use the available parent model independently. The skill's default Claude/Gemini runners and a separate-vendor judge are unavailable in this environment; their absence will remain explicit in the synthesis.

## Grounding

The current PostgreSQL model stores one JSONB payload and sort metadata per configuration. The retained publication has 319,352 results, and its complete local database allocates 936,752,819 bytes. Two 2 GiB reservations plus 1 GiB headroom require 6,305,461,939 bytes with that baseline. The 2 GiB reservation is itself an estimate, not a demonstrated bound for every million-result query.

Neon Free source browsing, restricted permissions, private Cloud Run deployment, idle reconciliation, R2 backup and managed restore have passed their scoped rehearsals. New searches remain paused. PostgreSQL should retain durable query identity, permissions, admission, leases, dispatch and publication metadata. The redesign may move bulky immutable result material to private R2 without exposing downloads, bucket credentials or signed object URLs to visitors.

The API exposes partial results in discovery order and completed results sorted by active traits, total cost or champion names, with configuration key as the tie-breaker. Page size ranges from 1 to 200. Retain complete/incomplete distinctions and existing saved-link behavior.

The cost review must include backup retention, operation rounding, transfer out of Google, failed attempts and orphan objects. Avoid a small-object design whose copies exhaust R2's Class A free allowance, and avoid downloading an entire result set for every API request. A hard byte/request allowance must reject work honestly without publishing truncated results as complete.
