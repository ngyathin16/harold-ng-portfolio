ALTER TABLE research.query_runs ADD COLUMN dispatch_generation integer NOT NULL DEFAULT 0;
CREATE TABLE research.dispatch_control (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 paused boolean NOT NULL DEFAULT false
);
INSERT INTO research.dispatch_control(singleton) VALUES(true);
CREATE TABLE research.dispatch_outbox (
 run_id text PRIMARY KEY REFERENCES research.query_runs(id) ON DELETE CASCADE,
 generation integer NOT NULL CHECK(generation>0),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','reserved','done')),
 launch_attempts integer NOT NULL DEFAULT 0,
 eligible_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE research.execution_reservations (
 id text PRIMARY KEY,
 run_id text NOT NULL REFERENCES research.query_runs(id) ON DELETE CASCADE,
 generation integer NOT NULL,
 state text NOT NULL CHECK(state IN ('launching','uncertain','running','terminal','rejected')),
 operation_name text, execution_name text,
 created_at timestamptz NOT NULL DEFAULT now(), checked_at timestamptz,
 expires_at timestamptz NOT NULL DEFAULT now()+interval '15 minutes',
 claimed_at timestamptz, outcome text
);
CREATE INDEX dispatch_eligible ON research.dispatch_outbox(eligible_at) WHERE state='pending';
CREATE INDEX execution_outstanding ON research.execution_reservations(state,created_at);
CREATE UNIQUE INDEX one_generation_execution ON research.execution_reservations(run_id,generation)
 WHERE state IN ('launching','uncertain','running');
UPDATE research.query_runs SET dispatch_generation=1 WHERE state='queued';
INSERT INTO research.dispatch_outbox(run_id,generation)
 SELECT id,dispatch_generation FROM research.query_runs WHERE state='queued';
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='research_dispatcher') THEN
  CREATE ROLE research_dispatcher NOLOGIN;
 END IF;
END $$;
GRANT USAGE ON SCHEMA research TO research_dispatcher;
GRANT SELECT ON research.query_runs,research.query_subscriptions TO research_dispatcher;
GRANT UPDATE ON research.query_runs TO research_dispatcher;
CREATE POLICY dispatcher_runs ON research.query_runs TO research_dispatcher USING(true) WITH CHECK(true);
CREATE POLICY dispatcher_subscriptions ON research.query_subscriptions FOR SELECT TO research_dispatcher USING(true);
REVOKE ALL ON research.dispatch_control,research.dispatch_outbox,research.execution_reservations FROM PUBLIC;
ALTER TABLE research.dispatch_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.dispatch_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.execution_reservations ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON research.dispatch_control,research.dispatch_outbox,research.execution_reservations TO research_api,research_worker,research_dispatcher;
GRANT INSERT,UPDATE ON research.dispatch_outbox TO research_api,research_dispatcher;
GRANT INSERT,UPDATE ON research.execution_reservations TO research_dispatcher;
GRANT UPDATE ON research.execution_reservations TO research_worker;
CREATE POLICY dispatch_control_read ON research.dispatch_control FOR SELECT TO research_api,research_worker,research_dispatcher USING(true);
CREATE POLICY dispatch_outbox_runtime ON research.dispatch_outbox TO research_api,research_worker,research_dispatcher USING(true) WITH CHECK(true);
CREATE POLICY dispatch_reservations_runtime ON research.execution_reservations TO research_api,research_worker,research_dispatcher USING(true) WITH CHECK(true);
GRANT ALL ON research.dispatch_control,research.dispatch_outbox,research.execution_reservations TO service_role;
CREATE POLICY dispatch_control_operator ON research.dispatch_control TO service_role USING(true) WITH CHECK(true);
CREATE POLICY dispatch_outbox_operator ON research.dispatch_outbox TO service_role USING(true) WITH CHECK(true);
CREATE POLICY dispatch_reservations_operator ON research.execution_reservations TO service_role USING(true) WITH CHECK(true);
