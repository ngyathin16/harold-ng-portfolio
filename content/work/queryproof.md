---
title: QueryProof
slug: queryproof
summary: A SQL review workbench that checks a number against its definition and shows the rows behind a misleading result.
status: Completed · runs locally
period: November 2026
role: Product direction and implementation with AI assistance
workType: Personal project
contribution: Metric contracts, SQL review workflow, independent calculations, model evaluation, and evidence design; developed with OpenAI Codex assistance.
repository: https://github.com/ngyathin16/queryproof
cover: /images/queryproof.webp
coverWidth: 1440
coverHeight: 2385
coverAlt: QueryProof showing an original revenue total of 1,110 USD, a reference total of 400 USD, and duplicated orders with their contribution to the discrepancy.
coverCaption: The running local application, captured 17 November 2026. Synthetic commerce records; the deterministic review runs without an AI model.
order: 1
technologies: [Python, DuckDB, SQLGlot, SQLite, React, TypeScript, Ollama]
---

## The problem

A SQL query can run successfully and still support the wrong decision. Join orders to both line items and campaign touches, then sum order totals, and the repeated rows can make revenue look much higher than it is. The SQL engine has done what it was asked; the analysis has lost its intended grain.

QueryProof lets an analyst state what the metric means, run a query against a pinned dataset, and inspect evidence before accepting a revision. The default workflow needs no account or API key.

Completed in November 2026, with public source, a captioned walkthrough and reproducible evaluation evidence. The [acceptance record](https://github.com/ngyathin16/queryproof/blob/main/docs/ACCEPTANCE.md) maps the delivered features to their checks and scope limits.

## A number with evidence

The bundled fictional commerce query reports **1,110 USD**. A separate row-based calculation of the confirmed metric returns **400 USD**. Those values come from executing the fixture, rather than a hardcoded demonstration. Three duplicated orders explain the excess contribution.

The reviewer can inspect each order's multiplicity, the relevant SQL, the metric clause and a reproducible diagnostic query. A candidate repair is executed before acceptance. The original SQL, contract and snapshot remain available in history; editing an input marks the earlier result as stale. JSON and standalone HTML reports make the evidence readable outside the application.

[Watch the captioned walkthrough](https://raw.githubusercontent.com/ngyathin16/queryproof/main/docs/walkthrough.mp4) or [read its captions](https://github.com/ngyathin16/queryproof/blob/main/docs/walkthrough.vtt). The recording captures the real local workflow on 16 November; it is recorded evidence, not a hosted executable application.

## Engineering decisions

- **Intent is explicit.** The contract names the entity grain, stable key, measure, grouping, relationships, population, time interval, null policy and numerical tolerances. Missing meaning produces “needs definition.”
- **Execution and correctness are separate.** SQLGlot checks a bounded grammar; a disposable DuckDB worker executes the query with external access disabled. An independent Python row/Decimal calculation supplies the reference. Complete groups and populations are compared before the interface truncates them.
- **Evidence survives revisions.** SQLite stores immutable review inputs and results. Cancellation terminates the worker, interrupted reviews remain interrupted, and reports exclude source-row examples unless selected.
- **AI advice is tested.** An optional local Qwen model can inspect bounded diagnostic tools and propose SQL. Its revision goes through the same restrictions and checks as manually entered SQL. A provider failure leaves the deterministic review usable.

## What the evaluation found

The second evaluation version freezes 60 new cases spanning ledgers, attribution bridges, ratios, device readings, inventory and session averages, alongside 60 development cases. On its first execution, the detector found **23 of 24 incorrect analyses**, accepted all 12 valid analyses, and kept all 24 underspecified or unsupported cases unverified.

The miss mattered: a zero numerator concealed a wrong ratio denominator. Adding a denominator witness fixed it; the subsequent regression run matches all 60 statuses. All **180 diagnostic counts** reproduce, and **48 declared repair solutions** across development and evaluation match independent grouped results and populations. The first failure remains in the published evidence. Once inspected, those cases are regression evidence rather than a continuing unseen test.

The complete local-model experiment keeps all **144 trials**, including failures: **12/72 successful repairs without diagnostic tools**, compared with **57/72 using QueryProof tools**. Tools used more time and input tokens. This is one fixed synthetic experiment with repeated temperature-zero trials, not a general model-accuracy claim.

Better repair counts did not make the explanations reliable. The agent rubric review found misleading facts in **12 of 60 completed tool-assisted explanations**, including nine that accompanied snapshot-correct repairs. For example, explanations claimed to remove a filter or join reference absent from the original query. The application therefore keeps executed evidence separate from model prose. No human review is implied.

[Read the raw results, failures and explanation review](https://github.com/ngyathin16/queryproof/blob/main/docs/VERIFICATION.md). The measured runtime/evaluator revision is `5e7e97b`.

## Verification and limits

A clean checkout passed **45 tests on Windows and Ubuntu**, plus the Windows interface build. Browser checks exercised the actual service: **21 workflow checks** and **12 views at 360, 768 and 1440 pixels**, including keyboard navigation and automated accessibility checks. No representative human usability study was available.

On a Core Ultra 9 desktop, 30 warm deterministic reviews of the standard 100,000-row fixture had **774 ms p95**, with no failed runs. The timed path includes worker startup, SQL execution and reference calculation; browser rendering and CSV import are separate. This is a desktop interaction measurement, not a laptop or production-scale claim.

Measures and grouping dimensions come from the declared base table; relationships may traverse declared paths. Timestamps use UTC. Broader contracts and SQL outside the supported grammar remain explicitly unsupported. The Linux memory guard samples resident memory; this localhost application is not a production query sandbox or a warehouse connector.

## Run it

The [repository README](https://github.com/ngyathin16/queryproof#run-locally) documents a fresh checkout with Python, uv and Node 24:

```sh
git clone https://github.com/ngyathin16/queryproof.git
cd queryproof
uv sync --frozen
cd ui
npm ci
npm run build
cd ..
uv run queryproof
```

Open `http://127.0.0.1:4317`. Optional live assistance uses a separately installed local Ollama model. Default analysis is free and runs without a model or credential.

## Contribution and sources

Harold Ng specified the product direction and reviews its engineering decisions. Implementation, fictional fixtures, evaluation tooling and automated verification were developed with OpenAI Codex assistance. No employer or customer data is included.

The project adapts the data-semantics problem described in [OpenAI's in-house data-agent article](https://openai.com/index/inside-our-in-house-data-agent/) and the tool/evaluation lessons in Anthropic's [tool-design guidance](https://www.anthropic.com/research/writing-tools-for-agents) and [agent-evaluation guidance](https://www.anthropic.com/research/demystifying-evals-for-ai-agents). No affiliation or endorsement is implied.
