---
title: IFC-GPT v2
slug: ifc-gpt
summary: From a building description to an inspectable 3D model, with planning and checks along the way.
status: Public prototype
period: 'April 2026'
workType: Personal project
role: Author of the public code and visual material
contribution: Agent workflow, IFC generation, validation, API, and browser interface.
repository: https://github.com/ngyathin16/ifc-gpt2.0
technologies: [Python, LangGraph, FastAPI, IfcOpenShell, Next.js]
cover: /images/ifc-gpt-xray.webp
coverWidth: 1440
coverHeight: 900
coverAlt: IFC-GPT browser interface showing the floors, walls, and windows of a synthetic three-storey building in X-Ray view.
coverCaption: Actual prototype, captured 11 November 2026. A hand-authored plan was built into an IFC file and opened in the viewer; this capture does not use a live AI call.
order: 4
---

## The problem

A building description is a starting point, but it leaves dimensions, geometry, and relationships open to interpretation. For someone exploring a building concept, the useful next step is a model they can inspect, with the assumptions and failures available for review.

IFC-GPT connects that description to IFC, an open format for building information models. The public prototype explores this workflow in the context of Hong Kong Modular Integrated Construction.

## My contribution

I authored the public prototype's code and visual material: the agent workflow, building primitives, validation steps, API, and browser interface. This case study covers that repository. My professional work and contributions to co-developed platforms are described separately in my experience.

## The approach

The workflow separates interpretation from geometry. An agent turns the request into a structured building plan. Python functions create walls, slabs, windows, and other elements from that plan. Separate checks inspect the plan and the resulting IFC file before a bounded repair loop and export.

<ol class="flow-diagram" aria-label="IFC-GPT workflow">
  <li>Describe &amp; clarify</li>
  <li>Structure the plan</li>
  <li>Build IFC geometry</li>
  <li>Check &amp; repair</li>
  <li>Export &amp; inspect</li>
</ol>

LangGraph coordinates the steps, FastAPI exposes the workflow, and a browser viewer lets the user inspect the file. The representation between planning and building is a useful boundary: the geometry functions can be exercised independently of a model call.

## Decisions and tradeoffs

**A structured plan before geometry.** A schema gives the builder concrete element types, dimensions, storeys, and references. It makes mistakes easier to locate than a single opaque generation step, although a schema alone cannot establish that a design makes sense.

**Checks at several levels.** The code checks plan relationships, IFC schema, selected property requirements, and semantic conditions. Each layer asks a different question; passing one does not imply that the others pass.

**A bounded repair loop.** Limiting retries prevents an indefinite loop, but the current export contract still needs work: the graph can export after exhausting its repair budget. A file being downloadable is therefore not evidence that all validation succeeded.

## What I verified

On 11 November 2026, the public revision [`f781ae9`](https://github.com/ngyathin16/ifc-gpt2.0/tree/f781ae94b3f318b103a13feb4a02868a1063cbf0) was checked locally. A focused set of **99 tests passed**, covering plan checks, build dispatch, walls, slabs, doors, windows, and export behavior. This was a selected regression run, not the complete repository suite or a measure of generated-design quality.

For the screenshot, a self-authored synthetic plan was passed through the real builder. The resulting file contained **3 storeys, 12 walls, 30 windows, and 4 slabs**. It opened in the production browser interface; fit, solid, wireframe, and X-Ray controls were exercised without page exceptions. No AI call or employer model was used for this evidence.

The same fixture passed the plan and IFC schema checks but **failed one of three included IDS property specifications**. The validator also warned that there was no `IfcRoof` entity: the top surface was a roof slab. The overall validation result was a failure. A visible model is useful evidence of the generation and viewing path, while these results show the limits of treating successful rendering as correctness.

## Limitations

- Natural-language generation, voice input, authentication, and repair using a live model were not exercised in this verification. Their presence in the code is not an end-to-end result.
- Modification currently regenerates a model rather than reliably editing one existing element.
- The included property checks do not establish building-code compliance or certification. The specification named “Fire doors must have fire rating” actually checks an `IsExternal` property, so its label overstates its scope.
- The public production build requires Supabase configuration to prerender its authentication pages. This capture used a clearly unused localhost configuration; authentication was not tested. A blank viewer was observed in development mode, while the production build rendered the fixture.
- This is a repository prototype, with no claim of customers, production deployment, or measured business impact.

## Explore the source

- [Public repository and setup instructions](https://github.com/ngyathin16/ifc-gpt2.0)
- [Workflow graph at the verified revision](https://github.com/ngyathin16/ifc-gpt2.0/blob/f781ae94b3f318b103a13feb4a02868a1063cbf0/agent/graph.py)
- [IFC builder](https://github.com/ngyathin16/ifc-gpt2.0/blob/f781ae94b3f318b103a13feb4a02868a1063cbf0/agent/nodes/build.py)
- [Export behavior](https://github.com/ngyathin16/ifc-gpt2.0/blob/f781ae94b3f318b103a13feb4a02868a1063cbf0/agent/nodes/export.py)
