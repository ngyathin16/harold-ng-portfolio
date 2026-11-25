CREATE TABLE research.capacity_sample (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 used_bytes bigint NOT NULL CHECK(used_bytes>=0),
 limit_bytes bigint NOT NULL CHECK(limit_bytes>0), measured_at timestamptz NOT NULL
);
CREATE TABLE research.capacity_reservations (
 run_id text PRIMARY KEY REFERENCES research.query_runs(id) ON DELETE CASCADE,
 bytes bigint NOT NULL CHECK(bytes>0)
);
ALTER TABLE research.capacity_sample ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.capacity_reservations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON research.capacity_sample,research.capacity_reservations FROM PUBLIC;
GRANT SELECT ON research.capacity_sample,research.capacity_reservations TO research_api,research_worker,research_dispatcher;
GRANT INSERT,UPDATE ON research.capacity_reservations TO research_api;
CREATE POLICY capacity_read ON research.capacity_sample FOR SELECT TO research_api,research_worker,research_dispatcher USING(true);
CREATE POLICY reservation_read ON research.capacity_reservations FOR SELECT TO research_api,research_worker,research_dispatcher USING(true);
CREATE POLICY reservation_insert ON research.capacity_reservations FOR INSERT TO research_api WITH CHECK(true);
CREATE POLICY reservation_update ON research.capacity_reservations FOR UPDATE TO research_api USING(true) WITH CHECK(true);
GRANT ALL ON research.capacity_sample,research.capacity_reservations TO service_role;
CREATE POLICY capacity_operator ON research.capacity_sample TO service_role USING(true) WITH CHECK(true);
CREATE POLICY reservations_operator ON research.capacity_reservations TO service_role USING(true) WITH CHECK(true);
