ALTER TABLE research.query_runs ADD COLUMN result_generation_id text;
CREATE SEQUENCE research.result_discovery_blocks INCREMENT BY 1000000 MINVALUE 1 MAXVALUE 9223372036853775807;
CREATE TABLE research.result_generations (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{32}$'),
 run_id text NOT NULL REFERENCES research.query_runs(id) ON DELETE CASCADE,
 dictionary jsonb,
 source_fingerprint jsonb,
 visible_count integer NOT NULL DEFAULT 0 CHECK(visible_count BETWEEN 0 AND 1000000),
 tail bytea NOT NULL DEFAULT '' CHECK(octet_length(tail)<102400 AND octet_length(tail)%25=0),
 complete_orders jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(run_id,id)
);
ALTER TABLE research.query_runs ADD CONSTRAINT result_generation_owner
 FOREIGN KEY(id,result_generation_id) REFERENCES research.result_generations(run_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE research.result_segments (
 generation_id text NOT NULL REFERENCES research.result_generations(id) ON DELETE CASCADE,
 first_row integer NOT NULL CHECK(first_row>=0 AND first_row%4096=0 AND first_row<1000000),
 object jsonb NOT NULL,
 PRIMARY KEY(generation_id,first_row)
);
CREATE TABLE research.result_object_intents (
 generation_id text NOT NULL REFERENCES research.result_generations(id) ON DELETE CASCADE,
 key text PRIMARY KEY CHECK(length(key)<=512),
 size bigint NOT NULL CHECK(size BETWEEN 0 AND 26214400),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 state text NOT NULL DEFAULT 'planned' CHECK(state IN ('planned','verified','deleted')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX result_intent_generation ON research.result_object_intents(generation_id);
REVOKE ALL ON research.result_generations,research.result_segments,research.result_object_intents FROM PUBLIC;
ALTER TABLE research.result_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.result_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.result_object_intents ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON research.result_generations,research.result_segments TO research_api,research_worker;
GRANT INSERT,UPDATE ON research.result_generations TO research_worker;
GRANT INSERT ON research.result_segments TO research_worker;
GRANT SELECT,INSERT,UPDATE ON research.result_object_intents TO research_worker;
GRANT USAGE,SELECT ON SEQUENCE research.result_discovery_blocks TO research_worker;
CREATE POLICY result_generation_read ON research.result_generations FOR SELECT TO research_api,research_worker USING(true);
CREATE POLICY result_generation_write ON research.result_generations TO research_worker USING(true) WITH CHECK(true);
CREATE POLICY result_segment_read ON research.result_segments FOR SELECT TO research_api,research_worker USING(true);
CREATE POLICY result_segment_write ON research.result_segments FOR INSERT TO research_worker WITH CHECK(true);
CREATE POLICY result_intent_worker ON research.result_object_intents TO research_worker USING(true) WITH CHECK(true);
GRANT ALL ON research.result_generations,research.result_segments,research.result_object_intents TO service_role;
GRANT USAGE,SELECT ON SEQUENCE research.result_discovery_blocks TO service_role;
CREATE POLICY result_generation_operator ON research.result_generations TO service_role USING(true) WITH CHECK(true);
CREATE POLICY result_segment_operator ON research.result_segments TO service_role USING(true) WITH CHECK(true);
CREATE POLICY result_intent_operator ON research.result_object_intents TO service_role USING(true) WITH CHECK(true);
