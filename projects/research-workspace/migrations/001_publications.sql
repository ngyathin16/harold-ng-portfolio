BEGIN;
CREATE SCHEMA IF NOT EXISTS research;
CREATE SCHEMA IF NOT EXISTS research_data;
REVOKE ALL ON SCHEMA research,research_data FROM PUBLIC;
GRANT USAGE ON SCHEMA research,research_data TO anon,authenticated,service_role;

CREATE TABLE research.datasets (
  id text PRIMARY KEY,
  name text NOT NULL,
  description text NOT NULL,
  adapter text NOT NULL,
  capabilities jsonb NOT NULL,
  latest_publication_id text
);
CREATE TABLE research.publications (
  id text PRIMARY KEY,
  dataset_id text NOT NULL REFERENCES research.datasets(id),
  snapshot_id text NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published')),
  manifest jsonb NOT NULL,
  scope jsonb NOT NULL,
  source_sha256 text NOT NULL,
  bundle_sha256 text NOT NULL,
  schema_fingerprint text NOT NULL,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id,dataset_id,snapshot_id),
  UNIQUE(dataset_id,id),
  UNIQUE(dataset_id,snapshot_id,bundle_sha256),
  CHECK ((status='published')=(published_at IS NOT NULL))
);
ALTER TABLE research.datasets ADD CONSTRAINT dataset_latest_scope FOREIGN KEY(id,latest_publication_id) REFERENCES research.publications(dataset_id,id);
CREATE TABLE research.table_definitions (
  publication_id text NOT NULL REFERENCES research.publications(id) ON DELETE CASCADE,
  name text NOT NULL,
  kind text NOT NULL CHECK(kind IN ('table','view')),
  columns jsonb NOT NULL,
  primary_key jsonb NOT NULL,
  row_count bigint NOT NULL CHECK(row_count>=0),
  logical_sha256 text NOT NULL,
  PRIMARY KEY(publication_id,name)
);
CREATE TABLE research.artifacts (
  publication_id text NOT NULL REFERENCES research.publications(id) ON DELETE CASCADE,
  id text NOT NULL,
  path text NOT NULL,
  storage_key text NOT NULL UNIQUE,
  sha256 text NOT NULL,
  size bigint NOT NULL CHECK(size>=0),
  media_type text NOT NULL,
  restored_sha256 text,
  restored_filename text,
  PRIMARY KEY(publication_id,id)
);

ALTER TABLE research.datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.publications ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.table_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.artifacts ENABLE ROW LEVEL SECURITY;
CREATE POLICY published_read ON research.publications FOR SELECT TO anon,authenticated USING(status='published');
CREATE POLICY published_read ON research.datasets FOR SELECT TO anon,authenticated USING
  (EXISTS(SELECT 1 FROM research.publications p WHERE p.dataset_id=datasets.id AND p.status='published'));
CREATE POLICY published_read ON research.table_definitions FOR SELECT TO anon,authenticated USING
  (EXISTS(SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));
CREATE POLICY published_read ON research.artifacts FOR SELECT TO anon,authenticated USING
  (EXISTS(SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));
GRANT SELECT ON research.datasets,research.publications,research.table_definitions,research.artifacts TO anon,authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA research TO service_role;
CREATE INDEX publications_dataset_versions ON research.publications(dataset_id,status,published_at DESC);
COMMIT;
