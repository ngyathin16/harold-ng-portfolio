"""Exercise publication, parity, RLS, replay and rollback against a local Postgres."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import psycopg
from psycopg.types.json import Jsonb

import import_snapshot as importer


def run(database_url):
    project = Path(os.environ.get("RESEARCH_SOURCE_PROJECT", str(Path(__file__).resolve().parents[2] / "tft-set-19"))).resolve()
    if not (project / "scripts/pipeline.py").is_file():
        raise ValueError("Set RESEARCH_SOURCE_PROJECT to the collector's tft-set-19 directory for publication fixtures")
    sys.path.insert(0, str(project / "tests"))
    sys.path.insert(0, str(project / "scripts"))
    import pipeline
    from test_catalog import fixture
    dataset = "verification-" + uuid.uuid4().hex[:12]
    assertions = []
    with tempfile.TemporaryDirectory(prefix="publication-check-") as directory:
        root = Path(directory)
        source = root / "source"
        shutil.copytree(project / "scripts", source / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        storage = importer.LocalStorage(root / "objects")
        publications = []
        try:
            for revision, hp in enumerate((600, 700)):
                payload = fixture()
                payload["units"][0]["stats"]["hp"] = hp
                raw = source / "data/raw/fixture.json"
                pipeline.write_json(raw, payload)
                capture = source / "data/runs/fixture.json"
                pipeline.write_json(capture, {"sources": [{"source_id": "metatft", "url": "https://example.invalid/fixture",
                    "status": "ok", "raw_path": "data/raw/fixture.json", "sha256": importer.sha(raw),
                    "retrieved_at": "2026-11-20T00:00:00Z", "media_type": "application/json"}]})
                snapshot = pipeline.build(source, capture)["snapshot_id"]
                bundle_root = root / f"bundle-{revision}"
                bundle = importer.prepare(source, snapshot, bundle_root, dataset, "Second dataset fixture", "generic")
                result = importer.publish(bundle_root, database_url, storage)
                publications.append(result["publication_id"])
                repeated = importer.publish(bundle_root, database_url, storage)
                assert repeated["status"] == "already_published"
                assertions.append(f"Snapshot {revision + 1}: all tables/views match; repeated import has no duplicates")
            with psycopg.connect(database_url) as conn:
                latest = conn.execute("SELECT latest_publication_id FROM research.datasets WHERE id=%s", (dataset,)).fetchone()[0]
                assert latest == publications[1]
                for publication, hp in zip(publications, (600, 700)):
                    rows = conn.execute("SELECT value FROM research_data.unit_stats WHERE publication_id=%s AND stat='hp'", (publication,)).fetchall()
                    assert rows == [(float(hp),)]
                    assert conn.execute("SELECT count(*) FROM research_data.v_units WHERE publication_id=%s", (publication,)).fetchone()[0] == 1
                assertions.append("Overlapping IDs in two retained snapshots do not collide or cross-join")
            broken = copy.deepcopy(bundle)
            broken["tables"]["entities"]["logical_sha256"] = "0" * 64
            unsigned = {k: v for k, v in broken.items() if k not in ("bundle_sha256", "publication_id")}
            broken["bundle_sha256"] = hashlib.sha256(importer.packed(unsigned).encode()).hexdigest()
            broken["publication_id"] = dataset + "--" + broken["snapshot_id"] + "--" + broken["bundle_sha256"][:16]
            importer.write(bundle_root / "bundle.json", broken)
            try:
                importer.publish(bundle_root, database_url, storage)
                raise AssertionError("Corrupt parity unexpectedly published")
            except ValueError as error:
                assert "parity failed" in str(error)
            with psycopg.connect(database_url) as conn:
                assert conn.execute("SELECT latest_publication_id FROM research.datasets WHERE id=%s", (dataset,)).fetchone()[0] == latest
                assert conn.execute("SELECT count(*) FROM research.publications WHERE id=%s", (broken["publication_id"],)).fetchone()[0] == 0
                assertions.append("Parity failure rolls back publication and preserves previous latest")
                draft = dataset + "-draft"
                conn.execute("INSERT INTO research.publications(id,dataset_id,snapshot_id,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                             (draft, dataset, "unpublished", Jsonb({}), Jsonb({}), "0" * 64, "0" * 64, "0" * 64))
                conn.execute("INSERT INTO research_data.entities SELECT %s,dataset_id,%s,entity_id,kind,source_key,name,description,description_raw,lifecycle_status,source_id,source_path,raw_json,variant_key,evidence_origin,corroboration_status FROM research_data.entities WHERE publication_id=%s", (draft, "unpublished", publications[0]))
            with psycopg.connect(database_url) as conn:
                conn.execute("SET LOCAL ROLE anon")
                assert conn.execute("SELECT count(*) FROM research.publications WHERE id=%s", (draft,)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM research_data.entities WHERE publication_id=%s", (draft,)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM research_data.research_catalog WHERE publication_id=%s", (draft,)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM research_data.v_units WHERE publication_id=%s", (latest,)).fetchone()[0] == 1
            with psycopg.connect(database_url) as conn:
                conn.execute("SET LOCAL ROLE anon")
                try:
                    conn.execute("UPDATE research.publications SET status='draft'")
                    raise AssertionError("Anonymous write was allowed")
                except psycopg.errors.InsufficientPrivilege:
                    conn.rollback()
            assertions.append("Anonymous reads exclude drafts through tables and views; writes are denied")
        finally:
            with psycopg.connect(database_url) as conn:
                conn.execute("UPDATE research.datasets SET latest_publication_id=NULL WHERE id=%s", (dataset,))
                conn.execute("DELETE FROM research.publications WHERE dataset_id=%s", (dataset,))
                conn.execute("DELETE FROM research.datasets WHERE id=%s", (dataset,))
    return {"passed": True, "checks": assertions, "scope": "Local Postgres; hosted storage and deployment require separate checks"}


if __name__ == "__main__":
    report = run(os.environ["DATABASE_URL"])
    print(json.dumps(report, indent=2))
    if len(sys.argv) > 1:
        importer.write(Path(sys.argv[1]), report)
