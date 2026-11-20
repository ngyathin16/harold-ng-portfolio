# Team solver

The solver searches unordered final boards, pins their source snapshot and ruleset,
and streams configurations for the hosted worker. It uses source IDs for selections;
display names never define champion identity. Ordinary and supported unique traits
use the same activation evaluator. Selecting Jazz only adds another trait constraint.

From `research-app/`, install the service requirements, then run:

```powershell
python -m unittest solver.test_solver -v
python -m solver build-rules --database '<collector-directory>/data/snapshots/<snapshot>/tft-set-19.sqlite' --output rules.json
python -m solver run --rules rules.json --query query.json --output runs/example
```

An example query (the IDs and version context are validated against `rules.json`):

```json
{
  "team_size": {"min": 8, "max": 8},
  "board_slots": 8,
  "include": ["tft19:unit:DA_Jhin19"],
  "cost": {"max": 4},
  "min_active_traits": 8,
  "include_locked": true
}
```

`normalize_query` fills all defaults and version identifiers. `query_key` includes
the complete normalized definition and solver version. The API must pin these
identifiers before enqueueing a run and only reuse an exact matching complete cache.
Do not reuse a Jazz-restricted result for the unrestricted example.

`solve(query, rules, on_result=..., on_progress=..., cancelled=...,
max_seconds=..., max_results=...)` returns a summary. Results are provisional until
`state == "complete"`. Cancellation, deadlines and result caps return explicit
incomplete states. Worker failures must also remain incomplete. Every emitted result
is recounted independently of the mutable search state. Cancellation and progress
are checked between bounded compiled DFS steps. NumPy is required; Numba accelerates
the same enumeration with a slow Python fallback when unavailable.

The CLI stores results in SQLite to sort the entire result set by active traits,
total cost, names and configuration key. It exports every row, even for incomplete
runs, with the completion state in `summary.json` and `manifest.json`. Structured
member and trait arrays remain JSON in CSV cells; `\N` means unknown total cost.
The JSON export preserves null. The manifest contains artifact checksums.

The rules record every assumption and evidence locator. K/DA uses a one-star
assumption. Missing slot usage is explicitly assumed to be one; Baron uses sourced
two-slot evidence. Sona and Seraphine use exclusive forms. Akali exclusivity comes
from curated source keys. Collab stays unknown rather than inventing a random group.
Conditional unsupported unique traits stay unknown; quest tiers are unavailable
without external progression. Beach Pop's first activation is explicitly inferred
from instrument thresholds. Zero-threshold placeholders cannot activate on an empty
board. A forbidden trait must be proven inactive, so unknown activation cannot
satisfy a prohibition. These rules describe all matches under recorded assumptions,
not a combat simulation or knowledge of unpublished behavior.

The tests compare exhaustive results against a separate `itertools.combinations`
oracle on small rosters and varied filters. They cover costs, slots, form exclusion,
trait tiers, unique and unknown traits, zero placeholders, contradictions, cache
scope and incomplete runs. Hosted runtime and concurrent service performance require
measurements on the selected deployment resources; a local test is not a hosted SLA.
