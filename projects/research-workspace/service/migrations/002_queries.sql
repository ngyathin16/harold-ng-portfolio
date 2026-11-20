CREATE TABLE IF NOT EXISTS research.rulesets (
 publication_id text NOT NULL REFERENCES research.publications(id),
 version text NOT NULL, rules jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(publication_id,version)
);
CREATE TABLE IF NOT EXISTS research.query_runs (
 id text PRIMARY KEY, publication_id text NOT NULL, ruleset_version text NOT NULL,
 solver_version text NOT NULL, query_hash text NOT NULL UNIQUE, definition jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','complete','cancelled','failed','resource_limited')),
 progress jsonb NOT NULL DEFAULT '{}', result_count bigint NOT NULL DEFAULT 0,
 champion_set_count bigint, attempts integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
 lease_owner text, lease_until timestamptz, error text, expires_at timestamptz,
 FOREIGN KEY(publication_id,ruleset_version) REFERENCES research.rulesets(publication_id,version)
);
CREATE INDEX IF NOT EXISTS query_queue ON research.query_runs(state,created_at);
CREATE TABLE IF NOT EXISTS research.query_subscriptions (
 id text PRIMARY KEY, run_id text NOT NULL REFERENCES research.query_runs(id),
 session_hash text NOT NULL, active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,session_hash)
);
CREATE TABLE IF NOT EXISTS research.query_results (
 run_id text NOT NULL REFERENCES research.query_runs(id) ON DELETE CASCADE,
 configuration_key text NOT NULL, champion_set_key text NOT NULL,
 active_trait_count integer NOT NULL, total_cost integer,
 champion_names text NOT NULL, discovery_index bigint GENERATED ALWAYS AS IDENTITY,
 result jsonb NOT NULL, PRIMARY KEY(run_id,configuration_key)
);
CREATE INDEX IF NOT EXISTS query_results_traits ON research.query_results(run_id,active_trait_count DESC,configuration_key);
CREATE INDEX IF NOT EXISTS query_results_cost ON research.query_results(run_id,total_cost,configuration_key);
CREATE INDEX IF NOT EXISTS query_results_names ON research.query_results(run_id,champion_names,configuration_key);
ALTER TABLE research.query_results ALTER COLUMN total_cost DROP NOT NULL;
CREATE TABLE IF NOT EXISTS research.query_artifacts (
 run_id text NOT NULL REFERENCES research.query_runs(id) ON DELETE CASCADE,
 format text NOT NULL CHECK(format IN ('json','csv')), storage_key text NOT NULL,
 size bigint NOT NULL, sha256 text NOT NULL, media_type text NOT NULL,
 generation_hash text NOT NULL, restored_filename text, restored_sha256 text,
 PRIMARY KEY(run_id,format)
);
ALTER TABLE research.query_artifacts ADD COLUMN IF NOT EXISTS generation_hash text NOT NULL DEFAULT '';
ALTER TABLE research.query_artifacts ADD COLUMN IF NOT EXISTS restored_filename text;
ALTER TABLE research.query_artifacts ADD COLUMN IF NOT EXISTS restored_sha256 text;
CREATE TABLE IF NOT EXISTS research.query_artifact_gc (
 storage_key text PRIMARY KEY, delete_after timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE research.query_artifact_gc ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON research.query_artifact_gc FROM PUBLIC;
CREATE TABLE IF NOT EXISTS research.request_limits (
 key text NOT NULL, window_start timestamptz NOT NULL, hits integer NOT NULL,
 PRIMARY KEY(key,window_start)
);
ALTER TABLE research.rulesets ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.query_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.query_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.query_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.request_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.query_artifacts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON research.query_artifacts FROM PUBLIC;
REVOKE ALL ON research.rulesets,research.query_runs,research.query_subscriptions,research.query_results,research.request_limits FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON research.query_artifact_gc FROM anon;
  REVOKE ALL ON research.query_artifacts FROM anon;
  REVOKE ALL ON research.rulesets,research.query_runs,research.query_subscriptions,research.query_results,research.request_limits FROM anon;
 END IF;
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN
  REVOKE ALL ON research.query_artifact_gc FROM authenticated;
  REVOKE ALL ON research.query_artifacts FROM authenticated;
  REVOKE ALL ON research.rulesets,research.query_runs,research.query_subscriptions,research.query_results,research.request_limits FROM authenticated;
 END IF;
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='service_role') THEN
  GRANT ALL ON research.query_artifact_gc TO service_role;
  GRANT ALL ON research.query_artifacts TO service_role;
  GRANT ALL ON research.rulesets,research.query_runs,research.query_subscriptions,research.query_results,research.request_limits TO service_role;
  GRANT USAGE,SELECT ON SEQUENCE research.query_results_discovery_index_seq TO service_role;
 END IF;
END $$;
