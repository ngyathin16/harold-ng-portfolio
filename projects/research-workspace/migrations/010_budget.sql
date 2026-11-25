CREATE TABLE research.budget_windows (
 id text PRIMARY KEY CHECK(length(id) BETWEEN 1 AND 128),
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 authority_database text NOT NULL DEFAULT current_database(),
 review_reference text NOT NULL CHECK(length(review_reference)>3),
 armed boolean NOT NULL DEFAULT false,
 CHECK(ends_at>starts_at AND ends_at<=starts_at+interval '32 days')
);
CREATE UNIQUE INDEX one_armed_budget ON research.budget_windows(armed) WHERE armed;
CREATE TABLE research.budget_metrics (
 window_id text NOT NULL REFERENCES research.budget_windows(id),
 metric text NOT NULL CHECK(metric IN ('search_attempts','operator_executions','service_invocations',
   'r2_class_a','r2_class_b','google_egress_bytes','api_response_bytes','gcs_class_a','cloud_control_calls')),
 ceiling bigint NOT NULL CHECK(ceiling>=0), used bigint NOT NULL DEFAULT 0 CHECK(used>=0 AND used<=ceiling),
 PRIMARY KEY(window_id,metric)
);
CREATE TABLE research.budget_storage (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 ceiling bigint NOT NULL CHECK(ceiling>=0), used bigint NOT NULL CHECK(used>=0 AND used<=ceiling)
);
CREATE TABLE research.budget_search_receipts (
 run_id text NOT NULL, generation integer NOT NULL CHECK(generation>0),
 window_id text NOT NULL REFERENCES research.budget_windows(id),
 attempts integer NOT NULL CHECK(attempts BETWEEN 1 AND 3), liability_bytes bigint NOT NULL CHECK(liability_bytes>0),
 released boolean NOT NULL DEFAULT false,
 PRIMARY KEY(run_id,generation)
);
CREATE TABLE research.budget_migration_receipts (
 generation_id text PRIMARY KEY CHECK(generation_id ~ '^[0-9a-f]{32}$'),
 run_id text NOT NULL,
 window_id text NOT NULL REFERENCES research.budget_windows(id),
 source_fingerprint_sha256 text NOT NULL CHECK(source_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
 retained_object_bytes bigint NOT NULL CHECK(retained_object_bytes BETWEEN 1 AND 79194304),
 liability_bytes bigint NOT NULL CHECK(liability_bytes=13*retained_object_bytes),
 released boolean NOT NULL DEFAULT false
);
CREATE TABLE research.budget_object_receipts (
 identity text PRIMARY KEY CHECK(identity ~ '^[0-9a-f]{64}$'),
 window_id text NOT NULL REFERENCES research.budget_windows(id),
 size bigint NOT NULL CHECK(size BETWEEN 0 AND 5000000000),
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 state text NOT NULL DEFAULT 'planned' CHECK(state IN ('planned','verified','deleted'))
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='research_budget_operator') THEN
  CREATE ROLE research_budget_operator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA research TO research_budget_operator;
ALTER TABLE research.budget_windows ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.budget_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.budget_storage ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.budget_search_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.budget_migration_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.budget_object_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON research.budget_windows,research.budget_metrics,research.budget_storage,research.budget_search_receipts,research.budget_migration_receipts,research.budget_object_receipts FROM PUBLIC;

CREATE FUNCTION research.budget_debit(requested_window text, charges jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 window_row research.budget_windows%ROWTYPE;
 item record;
 amount bigint;
 actor text := COALESCE(NULLIF(current_setting('role',true),'none'),session_user);
 operator_access boolean;
 allowed boolean;
BEGIN
 IF jsonb_typeof(charges)<>'object' OR charges='{}'::jsonb THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Invalid resource charge';
 END IF;
 SELECT * INTO window_row FROM research.budget_windows WHERE id=requested_window FOR UPDATE;
 IF NOT FOUND OR NOT window_row.armed OR window_row.authority_database<>current_database()
    OR clock_timestamp()<window_row.starts_at OR clock_timestamp()>=window_row.ends_at THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Resource allowance is unavailable or expired';
 END IF;
 operator_access := pg_has_role(actor,'service_role','MEMBER') OR pg_has_role(actor,'research_budget_operator','MEMBER');
 FOR item IN SELECT key,value FROM jsonb_each_text(charges) ORDER BY key LOOP
  IF item.value !~ '^[0-9]{1,18}$' THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Invalid resource charge';
  END IF;
  amount := item.value::bigint;
  allowed := operator_access
   OR (pg_has_role(actor,'research_api','MEMBER') AND item.key IN ('search_attempts','service_invocations','r2_class_b','google_egress_bytes','api_response_bytes','gcs_class_a','cloud_control_calls'))
   OR (pg_has_role(actor,'research_worker','MEMBER') AND item.key IN ('r2_class_a','r2_class_b','google_egress_bytes'))
   OR (pg_has_role(actor,'research_dispatcher','MEMBER') AND item.key IN ('service_invocations','gcs_class_a','cloud_control_calls','google_egress_bytes'));
  IF NOT allowed THEN
   RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Resource charge is not permitted for this role';
  END IF;
  UPDATE research.budget_metrics SET used=used+amount
   WHERE window_id=requested_window AND metric=item.key AND amount<=ceiling-used;
  IF NOT FOUND THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Resource allowance exhausted';
  END IF;
 END LOOP;
END $$;

CREATE FUNCTION research.budget_reserve_search(requested_window text, requested_run text, requested_generation integer,
 requested_attempts integer) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 receipt research.budget_search_receipts%ROWTYPE;
 liability bigint;
BEGIN
 IF requested_attempts NOT BETWEEN 1 AND 3 OR requested_generation<1 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Invalid search allowance';
 END IF;
 -- The same lock order is used by all charges and reservations.
 PERFORM 1 FROM research.budget_windows WHERE id=requested_window FOR UPDATE;
 SELECT * INTO receipt FROM research.budget_search_receipts
  WHERE run_id=requested_run AND generation=requested_generation;
 IF FOUND THEN
  IF receipt.released OR receipt.window_id<>requested_window OR receipt.attempts<>requested_attempts THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Search allowance identity conflicts';
  END IF;
  PERFORM research.budget_debit(requested_window,jsonb_build_object('search_attempts',0));
  RETURN;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM research.query_runs WHERE id=requested_run
     AND dispatch_generation=requested_generation AND state='queued') THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Search allowance requires the accepted generation';
 END IF;
 -- All attempt objects, eleven full backups and one isolated restore copy.
 liability := requested_attempts::bigint*117440512+12::bigint*79194304;
 PERFORM research.budget_debit(requested_window,jsonb_build_object('search_attempts',requested_attempts));
 UPDATE research.budget_storage SET used=used+liability WHERE singleton AND liability<=ceiling-used;
 IF NOT FOUND THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Retained object allowance exhausted';
 END IF;
 INSERT INTO research.budget_search_receipts(run_id,generation,window_id,attempts,liability_bytes)
  VALUES(requested_run,requested_generation,requested_window,requested_attempts,liability);
END $$;

CREATE FUNCTION research.budget_authorize_launch(requested_window text, requested_run text,
 requested_generation integer, requested_attempt integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM research.budget_debit(requested_window,jsonb_build_object('cloud_control_calls',0));
 IF requested_attempt<1 OR NOT EXISTS(SELECT 1 FROM research.budget_search_receipts
   WHERE window_id=requested_window AND run_id=requested_run AND generation=requested_generation
   AND NOT released AND requested_attempt<=attempts) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Launch has no reserved search allowance';
 END IF;
END $$;

CREATE FUNCTION research.budget_reserve_migration(requested_window text, requested_generation text,
 retained_bytes bigint) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 receipt research.budget_migration_receipts%ROWTYPE;
 staged research.result_generations%ROWTYPE;
 fingerprint text;
 liability bigint;
 actor text := COALESCE(NULLIF(current_setting('role',true),'none'),session_user);
BEGIN
 IF NOT (pg_has_role(actor,'service_role','MEMBER') OR pg_has_role(actor,'research_budget_operator','MEMBER')) THEN
  RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Migration reservation requires an operator';
 END IF;
 IF requested_generation IS NULL OR requested_generation !~ '^[0-9a-f]{32}$'
    OR retained_bytes IS NULL OR retained_bytes NOT BETWEEN 1 AND 79194304 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Invalid migration storage allowance';
 END IF;
 PERFORM research.budget_debit(requested_window,jsonb_build_object('operator_executions',0));
 SELECT * INTO staged FROM research.result_generations WHERE id=requested_generation FOR SHARE;
 IF NOT FOUND OR staged.source_fingerprint IS NULL OR jsonb_typeof(staged.source_fingerprint)<>'object' THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Migration requires a staged source fingerprint';
 END IF;
 fingerprint := staged.source_fingerprint->>'sha256';
 IF fingerprint IS NULL OR fingerprint !~ '^[0-9a-f]{64}$' OR requested_generation<>
   left(encode(sha256(convert_to('research-result-migration-v1','UTF8') || decode('00','hex') ||
     convert_to(staged.run_id,'UTF8') || decode('00','hex') || convert_to(fingerprint,'UTF8')),'hex'),32) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Migration generation and source fingerprint disagree';
 END IF;
 SELECT * INTO receipt FROM research.budget_migration_receipts WHERE generation_id=requested_generation;
 IF FOUND THEN
  IF receipt.released OR receipt.run_id<>staged.run_id OR receipt.window_id<>requested_window OR receipt.retained_object_bytes<>retained_bytes
     OR receipt.source_fingerprint_sha256<>fingerprint THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Migration storage allowance identity conflicts';
  END IF;
  RETURN;
 END IF;
 liability := 13*retained_bytes;
 UPDATE research.budget_storage SET used=used+liability WHERE singleton AND liability<=ceiling-used;
 IF NOT FOUND THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Retained migration allowance exhausted';
 END IF;
 INSERT INTO research.budget_migration_receipts(generation_id,run_id,window_id,source_fingerprint_sha256,
   retained_object_bytes,liability_bytes) VALUES(requested_generation,staged.run_id,requested_window,fingerprint,retained_bytes,liability);
END $$;

CREATE FUNCTION research.budget_operator_window(requested_window text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor text := COALESCE(NULLIF(current_setting('role',true),'none'),session_user);
BEGIN
 IF NOT (pg_has_role(actor,'service_role','MEMBER') OR pg_has_role(actor,'research_budget_operator','MEMBER')) THEN
  RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='Storage receipts require an operator';
 END IF;
 PERFORM research.budget_debit(requested_window,jsonb_build_object('operator_executions',0));
END $$;

CREATE FUNCTION research.budget_prepare_object(requested_window text, object_identity text,
 object_size bigint, object_sha256 text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE receipt research.budget_object_receipts%ROWTYPE;
BEGIN
 PERFORM research.budget_operator_window(requested_window);
 IF object_identity IS NULL OR object_identity !~ '^[0-9a-f]{64}$' OR object_sha256 IS NULL
    OR object_sha256 !~ '^[0-9a-f]{64}$' OR object_size IS NULL OR object_size NOT BETWEEN 0 AND 5000000000 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Invalid physical object identity';
 END IF;
 SELECT * INTO receipt FROM research.budget_object_receipts WHERE identity=object_identity FOR UPDATE;
 IF FOUND THEN
  IF receipt.state='deleted' OR receipt.size<>object_size OR receipt.sha256<>object_sha256 THEN
   RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Physical object identity is retired or conflicts';
  END IF;
  RETURN receipt.state;
 END IF;
 UPDATE research.budget_storage SET used=used+object_size WHERE singleton AND object_size<=ceiling-used;
 IF NOT FOUND THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Persistent physical storage allowance exhausted';
 END IF;
 INSERT INTO research.budget_object_receipts(identity,window_id,size,sha256)
   VALUES(object_identity,requested_window,object_size,object_sha256);
 RETURN 'new';
END $$;

CREATE FUNCTION research.budget_get_object_receipt(requested_window text, object_identity text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE receipt jsonb;
BEGIN
 PERFORM research.budget_operator_window(requested_window);
 IF object_identity IS NULL OR object_identity !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Invalid physical object identity';
 END IF;
 SELECT to_jsonb(r) INTO receipt FROM research.budget_object_receipts r WHERE identity=object_identity;
 RETURN receipt;
END $$;

CREATE FUNCTION research.budget_verify_object(requested_window text, object_identity text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM research.budget_operator_window(requested_window);
 UPDATE research.budget_object_receipts SET state='verified'
   WHERE identity=object_identity AND state IN ('planned','verified');
 IF NOT FOUND THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='No verifiable physical object receipt';
 END IF;
END $$;

CREATE FUNCTION research.budget_delete_object(requested_window text, object_identity text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE receipt research.budget_object_receipts%ROWTYPE;
BEGIN
 PERFORM research.budget_operator_window(requested_window);
 SELECT * INTO receipt FROM research.budget_object_receipts WHERE identity=object_identity FOR UPDATE;
 IF NOT FOUND OR receipt.state='planned' THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Uncertain physical objects cannot release storage';
 END IF;
 IF receipt.state='deleted' THEN RETURN 0; END IF;
 UPDATE research.budget_storage SET used=used-receipt.size WHERE singleton AND used>=receipt.size;
 IF NOT FOUND THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Persistent storage accounting is inconsistent';
 END IF;
 UPDATE research.budget_object_receipts SET state='deleted' WHERE identity=object_identity;
 RETURN receipt.size;
END $$;

CREATE FUNCTION research.budget_retire_run(requested_window text, requested_run text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE run research.query_runs%ROWTYPE; liability bigint; released_bytes bigint := 0;
BEGIN
 -- Acceptance already owns the run before reserving a budget: preserve that order.
 SELECT * INTO run FROM research.query_runs WHERE id=requested_run FOR UPDATE;
 PERFORM research.budget_operator_window(requested_window);
 IF run.id IS NULL OR run.state NOT IN ('complete','cancelled','failed','resource_limited')
    OR run.expires_at IS NULL OR run.expires_at>clock_timestamp()
    OR run.lease_owner IS NOT NULL OR run.lease_until IS NOT NULL OR run.result_generation_id IS NOT NULL
    OR EXISTS(SELECT 1 FROM research.execution_reservations WHERE run_id=requested_run AND state NOT IN ('terminal','rejected'))
    OR EXISTS(SELECT 1 FROM research.result_generations WHERE run_id=requested_run) THEN
  RETURN 0;
 END IF;
 WITH released AS (
   UPDATE research.budget_search_receipts SET released=true WHERE run_id=requested_run AND NOT released RETURNING liability_bytes
 ) SELECT COALESCE(sum(liability_bytes),0) INTO liability FROM released;
 released_bytes := liability;
 WITH released AS (
   UPDATE research.budget_migration_receipts SET released=true WHERE run_id=requested_run AND NOT released RETURNING liability_bytes
 ) SELECT COALESCE(sum(liability_bytes),0) INTO liability FROM released;
 released_bytes := released_bytes+liability;
 UPDATE research.budget_storage SET used=used-released_bytes WHERE singleton AND used>=released_bytes;
 IF NOT FOUND THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Persistent storage accounting is inconsistent';
 END IF;
 RETURN released_bytes;
END $$;

REVOKE ALL ON FUNCTION research.budget_debit(text,jsonb),research.budget_reserve_search(text,text,integer,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION research.budget_authorize_launch(text,text,integer,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION research.budget_reserve_migration(text,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.budget_debit(text,jsonb) TO research_api,research_worker,research_dispatcher,service_role,research_budget_operator;
GRANT EXECUTE ON FUNCTION research.budget_reserve_search(text,text,integer,integer) TO research_api,service_role;
GRANT EXECUTE ON FUNCTION research.budget_authorize_launch(text,text,integer,integer) TO research_dispatcher,service_role;
GRANT EXECUTE ON FUNCTION research.budget_reserve_migration(text,text,bigint) TO service_role,research_budget_operator;
REVOKE ALL ON FUNCTION research.budget_operator_window(text),research.budget_prepare_object(text,text,bigint,text),
 research.budget_get_object_receipt(text,text),research.budget_verify_object(text,text),research.budget_delete_object(text,text),
 research.budget_retire_run(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.budget_operator_window(text),research.budget_prepare_object(text,text,bigint,text),
 research.budget_get_object_receipt(text,text),research.budget_verify_object(text,text),research.budget_delete_object(text,text),
 research.budget_retire_run(text,text) TO service_role,research_budget_operator;
