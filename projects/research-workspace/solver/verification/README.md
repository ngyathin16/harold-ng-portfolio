# Local solver verification

The final recorded rules are in `benchmark-rules.json`. Both runs exhausted their
eligible search spaces and independently recounted every returned board.

| Query | Configurations | Distinct champion sets | Local execution |
| --- | ---: | ---: | ---: |
| Eight champions, eight active traits, Jhin, cost at most four | 280,543 | 211,961 | 66.82 seconds |
| Same query plus Jazz's two-member breakpoint | 38,809 | 30,935 | 10.18 seconds |

These are single-run local diagnostics, not a hosted performance certification or
a comparison between implementations. The files record the exact snapshot, frozen
rules, normalized queries, code hashes, Python/NumPy/Numba versions and machine
platform. Timing includes enumeration and result recounts. Jazz additionally
recounts every returned configuration against the broader query. It excludes
database result persistence, complete exports, queue wait, network and concurrent
load. No completed-result cache was used. Both final states are `complete`; no
timeout or result cap was encountered. The earlier source snapshot's completeness
label remains `validated_with_gaps`.

Reproduce with `python -m solver.benchmark --database <snapshot.sqlite> --output
<fresh-report-directory>`. The runtime is a CPU-bound enumeration plus Python
result reconstruction on this local host; no hosted worker resources are selected
by these measurements. Hosted two-minute and concurrency requirements remain
unverified.

Reviewed semantic decisions:

- Native trait memberships and positive source tiers supply ordinary activation.
  Unique traits use the same counting rule and count at most once. Triggered
  combat effects are not simulated.
- Country explicitly requires bonus Health and Emo requires positioning. Neither
  is supplied by a final-board query, so their activation remains Unknown when
  their champion is present. Hype Man lacks a description and also stays Unknown.
- Collab's random membership is never filled in; its activation remains Unknown.
  Unknown cannot satisfy either a required-active or forbidden-active constraint.
- The K/DA description uses total star levels. The frozen baseline explicitly
  assumes one-star champions, so it does not silently treat all star levels alike.
- Style-five quest tiers are unavailable under the no-augment/no-external-progress
  baseline. A higher supported tier satisfies a lower requested tier.
- Beach Pop has a literal placeholder at zero. The positive first instrument
  threshold is an explicitly inferred replacement with its own evidence locator.
  Other zero placeholders do not activate automatically.
- Sona uses the chosen extra ability's complete trait list, not every band at once.
  Seraphine's three forms replace her unique trait. Their source paths identify the
  corresponding auxiliary record or extra ability. Akali exclusivity uses the
  curated source-key relationship, not matching display names.
- Baron uses two slots from the curated rule. Every fallback to one slot is marked
  as an assumption on the unit and in the shared ruleset.

`python -m unittest solver.test_solver -v` additionally checks a separate brute-force
oracle, varied filters, more than three trait memberships per champion, form
exclusion, cost and capacity boundaries, unique and unknown traits, zero
placeholders, query contradictions, bounded-step resumption, cancellation, result
caps and complete export round trips. The frozen rules define the scope of
"all matches"; these results do not claim exhaustive unpublished game knowledge.
