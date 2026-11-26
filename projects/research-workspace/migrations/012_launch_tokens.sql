-- Existing jobs.run reservations remain observation-only. New launches persist
-- a conditional Job update before any request capable of creating an execution.
ALTER TABLE research.execution_reservations
 ADD COLUMN launch_protocol text NOT NULL DEFAULT 'legacy'
  CHECK(launch_protocol IN ('legacy','token-v1')),
 ADD COLUMN launch_spec jsonb
  CHECK(launch_spec IS NULL OR (jsonb_typeof(launch_spec)='object'
        AND octet_length(launch_spec::text)<=65536)),
 ADD COLUMN launch_confirmed_at timestamptz;

CREATE UNIQUE INDEX one_unconfirmed_job_mutation
 ON research.execution_reservations ((1))
 WHERE launch_protocol='token-v1' AND launch_confirmed_at IS NULL
 AND state IN ('launching','uncertain','running');

-- A worker acknowledges its reservation, but cannot replace the durable
-- request or mark a cloud execution confirmed.
REVOKE UPDATE ON research.execution_reservations FROM research_worker;
GRANT UPDATE(state,claimed_at) ON research.execution_reservations TO research_worker;
