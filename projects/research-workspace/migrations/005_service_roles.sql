-- Apply after service/migrations/002_queries.sql. Grant these groups to separate
-- server login roles; never grant membership to browser roles.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='research_api') THEN CREATE ROLE research_api NOLOGIN; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='research_worker') THEN CREATE ROLE research_worker NOLOGIN; END IF;
END $$;
GRANT anon TO research_api,research_worker;
GRANT USAGE ON SCHEMA research,research_data TO research_api,research_worker;
GRANT SELECT ON research.rulesets,research.query_runs,research.query_subscriptions,research.query_results,research.request_limits TO research_api,research_worker;
GRANT INSERT,UPDATE ON research.query_runs,research.query_subscriptions,research.request_limits TO research_api;
GRANT DELETE ON research.query_results TO research_api;
GRANT SELECT,DELETE ON research.query_artifacts TO research_api;
GRANT SELECT,INSERT ON research.query_artifact_gc TO research_api;
GRANT UPDATE ON research.query_runs TO research_worker;
GRANT INSERT,DELETE ON research.query_results TO research_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON research.query_artifacts TO research_worker;
GRANT SELECT,INSERT,DELETE ON research.query_artifact_gc TO research_worker;
GRANT DELETE ON research.request_limits TO research_worker;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA research TO research_worker;
CREATE POLICY service_rules_read ON research.rulesets FOR SELECT TO research_api,research_worker USING
 (EXISTS(SELECT 1 FROM research.publications p WHERE p.id=publication_id AND p.status='published'));
CREATE POLICY service_jobs ON research.query_runs TO research_api,research_worker USING(true) WITH CHECK(true);
CREATE POLICY service_subscriptions ON research.query_subscriptions TO research_api,research_worker USING(true) WITH CHECK(true);
CREATE POLICY service_results ON research.query_results TO research_api,research_worker USING(true) WITH CHECK(true);
CREATE POLICY service_query_artifacts ON research.query_artifacts TO research_api,research_worker USING(true) WITH CHECK(true);
CREATE POLICY service_artifact_gc ON research.query_artifact_gc TO research_api,research_worker USING(true) WITH CHECK(true);
CREATE POLICY service_limits ON research.request_limits TO research_api,research_worker USING(true) WITH CHECK(true);
COMMIT;
