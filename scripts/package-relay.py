"""Build the public Relay Desk source download from an explicit file allowlist."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "downloads" / "relay-desk.zip"
FILES = """
.env.example
.gitignore
README.md
package.json
server.mjs
content/PROVENANCE.md
content/evaluation.json
content/examples.json
content/knowledge.json
docs/VERIFICATION.md
docs/CHANGELOG.md
docs/screenshots/desktop.png
docs/screenshots/evaluation.png
docs/screenshots/mobile.png
lib/analyze.mjs
lib/evaluation.mjs
lib/provider.mjs
lib/store.mjs
public/app.js
public/favicon.svg
public/index.html
public/styles.css
scripts/evaluate.mjs
test/backend.test.mjs
""".split()

START_HERE = """# Run this Relay Desk download

Extract the archive, install Node.js 24 or newer, and open a terminal inside
the extracted `relay-desk` folder (the folder containing `package.json`).

```sh
npm start
```

Open http://127.0.0.1:3210. No installation of npm packages, API credential,
or payment is required for the default extractive mode. Stop with Ctrl+C.
On Windows, use `npm.cmd` if PowerShell blocks the npm launcher.

Run the checks in that same folder:

```sh
npm test
npm run evaluate
```

The included README explains the workflow, architecture, optional model setup,
and limitations. Run its commands from the extracted folder containing package.json.

This is a local, single-user demonstration with a mock CRM and fictional data.
The download contains no local database, credentials, or configured live model.
The app creates its own `data` folder on first run. Do not host it publicly
without adding the access and data-handling controls described in the README.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    embedded = ROOT / "projects" / "support-workbench"
    parser.add_argument("--source", type=Path, default=embedded if embedded.is_dir() else ROOT.parent / "relay-desk",
                        help="Standalone Relay Desk checkout (defaults to the embedded or sibling project)")
    project = parser.parse_args().source.resolve()
    entries = {"START-HERE.md": START_HERE.encode("utf-8")}
    for name in FILES:
        source = project / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing or linked package input: {name}")
        if not source.resolve().is_relative_to(project):
            raise ValueError(f"Package input escaped the project: {name}")
        entries[name] = source.read_bytes()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".zip.tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in sorted(entries.items()):
                info = ZipInfo(f"relay-desk/{name}")
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        with ZipFile(temporary) as archive:
            assert archive.testzip() is None, "Archive integrity check failed"
            assert set(archive.namelist()) == {f"relay-desk/{name}" for name in entries}
            assert "relay-desk/.env" not in archive.namelist()
        temporary.replace(OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Packaged {len(entries)} files: {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
