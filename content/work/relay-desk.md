---
title: Relay Desk
slug: relay-desk
summary: A bilingual support workbench that keeps the source, the reply, and the human decision together.
status: Local working prototype
period: November 2026
role: Personal project, developed with AI assistance
workType: Personal project
contribution: Workflow, bilingual fixtures, integration handling, and evaluation; developed with AI assistance.
sourceDownload: /downloads/relay-desk.zip
cover: /images/relay-desk.webp
coverWidth: 1440
coverHeight: 1782
coverAlt: Relay Desk with an English support ticket, a source-linked reply, and human approval controls.
coverCaption: The running local workbench, using the free source-excerpt baseline. No live model call is shown.
order: 2
technologies: [JavaScript, Node.js, SQLite, Ollama]
---

## The problem

A plausible support reply can still be wrong. Before sending one, a support engineer needs to see its evidence, spot missing context, and decide whether it is ready for a customer. The handoff also needs to survive a failed integration without creating duplicate updates.

## The project

Relay Desk is a local workbench for Harbour, a fictional product with eight self-authored English and Traditional Chinese knowledge articles. A ticket becomes a draft with supporting sources or a visible request for more information. The operator can inspect the article, edit the response, and explicitly approve a local mock CRM handoff.

The default version runs without a credential or paid service. It uses bilingual phrase retrieval and source excerpts, not an LLM. Optional live mode is a separate, explicitly selected path.

## Decisions

- **A free, inspectable baseline.** Phrase retrieval returns source excerpts without calling a model. The interface says so. A real configured model can draft a response from those sources, but it does not replace the review step.
- **Clear state changes.** Editing the response revokes approval. Unsupported and blocked drafts cannot be delivered. A successful handoff is unique per draft; a failed attempt can be retried.
- **Simple persistence.** SQLite stores generated drafts, delivery attempts, and successful replies. Edits remain in the active editor until successful delivery; reopening an undelivered draft restores its original text.
- **One evaluator.** The browser report and command-line report use the same analyzer and fixed expectations. Failures are visible, and the CLI returns a failing exit status.

## Evidence and its scope

On 11 November 2026, all **36 test cases passed**, returning the expected status and exact source IDs. The cases cover English and Traditional Chinese, including questions the system can answer, unknown requests, missing details, ambiguous wording, and instruction attacks. These are fixed, synthetic cases used during development. They help catch regressions, but don't measure accuracy on unseen requests or the quality of generated replies.

All **9 automated behavior tests passed** using real HTTP requests and SQLite. They check request validation, approval and editing, duplicate delivery, and retrying a failed handoff. They also check that records survive a restart and that the system handles invalid provider responses, upstream errors, and timeout limits.

A local test with Qwen 3.5 9B returned a structured reply about invitations with links to its sources. This confirmed that the optional model integration worked for that request. A single reply isn't enough to judge the model's overall answer quality.

Browser checks covered approval, editing, opening source articles, retrying a failed delivery, and viewing history. They also covered English and Traditional Chinese content and the evaluation view, with layouts checked at 360, 768, and 1440 pixels wide. The final automated test results are recorded in the project's delivery record.

<figure>
  <a href="/images/relay-evaluation.webp"><img src="/images/relay-evaluation.webp" alt="Relay Desk evaluation view showing 36 cases, 36 passed, and zero failed, with individual expected and actual outcomes." width="1440" height="1080" loading="lazy" /></a>
  <figcaption>The fixed development checks, visible in the running application. Open the image for the full view.</figcaption>
</figure>

## How it fits together

```text
Ticket + language
       ↓
Phrase retrieval → self-authored Harbour articles
       ↓
Source-excerpt draft (or an explicitly selected provider)
       ↓
Source review → edit → approve
       ↓
Mock CRM handoff → SQLite history
       ↳ failed attempt → retry with duplicate protection
```

The browser talks to a local Node.js HTTP server. The server validates incoming requests and provider responses, keeps any provider keys server-side, and persists drafts, attempts, and successful handoffs in SQLite. Editing a reply clears its approval; delivery rechecks eligibility on the server.

## Limits

This is a single-user local demonstration. Its CRM is a real local persistence workflow, not an external customer integration. The keyword baseline can miss paraphrases or select incomplete evidence. Source-ID validation does not prove that every generated sentence is correct, and attack detection is intentionally limited. There are no claimed customer deployments or business savings.

## Run and inspect

[Download the runnable source](/downloads/relay-desk.zip), extract it, and open a terminal in the `relay-desk` folder. With Node.js 24 installed:

```sh
npm start
```

Open `http://127.0.0.1:3210`. Select a sample in either language, generate a source-excerpt draft, inspect its sources, then edit and approve it. Select **Simulate a failed handoff** before delivery to inspect a failed attempt and retry. The Knowledge and Evaluation views show the supporting documents and fixed cases.

Run `npm test` for the behavior tests and `npm run evaluate` for the fixed case report. No package install or API credential is required. The archive includes the README, architecture, verification record, screenshots, and optional provider configuration. Its default runtime binds to localhost.

This is a new personal project from November 2026. The implementation and fictional fixtures were developed with AI assistance; no employer data is involved.
