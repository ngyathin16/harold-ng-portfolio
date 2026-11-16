# IFC-GPT v2 evidence

Verified 11 November 2026 (Hong Kong). This record supports the portfolio case study, not a release of the IFC application.

## Source and scope

- Fresh checkout of the public repository: https://github.com/ngyathin16/ifc-gpt2.0
- Revision: `f781ae94b3f318b103a13feb4a02868a1063cbf0`, commit dated 13 April 2026.
- Public application source was not edited. Its npm postinstall script refreshed the tracked viewer worker asset from the locked dependency.
- Existing local Python virtual-environment dependencies were reused, with imports resolved from the fresh checkout. This was not a clean Python dependency-install test.
- No local employer assets, `.env` files, or real building models were copied. `PYTHON_DOTENV_DISABLED=1` and empty AI key variables were used for the run.
- Data: a self-authored synthetic three-storey geometry fixture, generated during portfolio implementation. AI-assisted coding and browser automation produced this verification fixture and record. It is not a building design or a live model result.

## Observed results

| Check | Result |
| --- | --- |
| Selected Python regression suite | 99 passed, 0 failed across eight named test files |
| Fixture contents, parsed with IfcOpenShell | 3 storeys, 12 walls, 30 windows, 4 slabs |
| Plan checks | Pass, 0 errors, 0 warnings |
| IFC4 schema checks | Pass, 0 errors, 0 warnings |
| Included IDS checks | 2 of 3 pass; `Fire doors must have fire rating` fails |
| Semantic checks | 0 errors; 1 warning because an `IfcRoof` entity is absent (top surface is an `IfcSlab`) |
| Overall validation | **Fail** |
| Browser, production build | Open file, fit, solid, wireframe, and X-Ray exercised; 0 page exceptions |
| Geometry renderer log | 46 flat meshes, 136 meshes, 2,506 IFC entities |

The IDS label above is misleading: the actual public specification checks `Pset_DoorCommon.IsExternal`, not fire rating. The fixture has no doors. The invalid `custom.ids` file was logged and skipped by the application's validator. Passing schema and plan checks does not establish compliance or suitability for construction.

Browser: Microsoft Edge `157.0.4341.46`, headless Chromium via Playwright, viewport 1440 × 900, device scale factor 1, Windows. Both screenshots were visually inspected after capture. Files are `/images/ifc-gpt.webp` (solid) and `/images/ifc-gpt-xray.webp` (X-Ray); only WebP encoding was applied.

## Reproduce

Use a separate checkout at the revision above. Follow its README to install Python dependencies using `uv sync` and frontend dependencies using `npm ci` in `web`. The verification machine used Node.js 24.14.0 and an existing Python environment; dependency installation from scratch was not verified.

Run these tests from the public checkout, with dotenv loading disabled and no API credentials:

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:AZURE_OPENAI_API_KEY = ''
$env:OPENAI_API_KEY = ''
uv run pytest tests/test_build_node.py tests/test_build_type_dispatch.py tests/test_plan_checks.py tests/test_export_node.py tests/test_wall.py tests/test_slab.py tests/test_window.py tests/test_door.py -q
```

Copy [ifc-screenshot.py](fixtures/ifc-screenshot.py) into the checkout root and run `uv run python ifc-screenshot.py`. It generates the reproducible [plan](fixtures/ifc-plan.json), the IFC file in `workspace/portfolio-synthetic.ifc`, and a JSON result in `workspace/portfolio-result.json`. The script asserts the four entity counts above. It calls the builder and validator directly; it does not invoke the agent's LLM or repair loop.

Run the API bound to localhost:

```powershell
uv run uvicorn api.server:app --host 127.0.0.1 --port 8000
```

For the frontend, a build without Supabase configuration fails while prerendering `/login`. To inspect the local file viewer without setting up auth, this verification used explicitly nonfunctional localhost configuration:

```powershell
$env:NEXT_PUBLIC_SUPABASE_URL = 'http://127.0.0.1:54321'
$env:NEXT_PUBLIC_SUPABASE_ANON_KEY = 'portfolio-local-unused'
$env:NEXT_TELEMETRY_DISABLED = '1'
npm run build
npm run start -- --hostname 127.0.0.1 --port 3030
```

Open `http://127.0.0.1:3030`, choose **Open IFC File**, and select the generated fixture. Use fit, zoom, and view controls. The successful screenshots use the production build; the development server displayed a blank canvas despite ready controls and geometry logs. No public-source fix was made for that issue during portfolio implementation.

## Not established

Live natural-language generation, voice, authentication, floor-plan recognition, live repair, element modification, complete-suite health, throughput, construction compliance, mobile usability of this prototype, or deployment reliability. Do not turn the selected test count into an accuracy claim. The fresh run establishes only the specific behavior and limitations listed above.
