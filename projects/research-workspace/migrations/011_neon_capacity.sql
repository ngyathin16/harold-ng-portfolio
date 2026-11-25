CREATE FUNCTION research.measure_neon_branch() RETURNS TABLE(used_bytes bigint,limit_bytes bigint)
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE database_ids oid[]; database_count integer; measured_count integer;
 database_bytes bigint; shared_bytes bigint; actual_limit bigint;
BEGIN
 IF current_setting('server_version_num')::integer NOT BETWEEN 170000 AND 179999
    OR NOT EXISTS(SELECT 1 FROM pg_settings WHERE name='neon.max_cluster_size'
                  AND context='sighup' AND vartype='integer' AND unit='MB')
    OR (SELECT count(*) FROM pg_settings WHERE name IN ('neon.tenant_id','neon.timeline_id')
         AND context='postmaster' AND vartype='string' AND setting ~ '^[0-9a-f]{32}$')<>2
    OR NOT ('neon'=ANY(regexp_split_to_array(current_setting('shared_preload_libraries'),'\s*,\s*'))) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Qualified Neon branch measurement is unavailable';
 END IF;
 actual_limit := pg_size_bytes(current_setting('neon.max_cluster_size'));
 IF actual_limit<=0 OR EXISTS(SELECT 1 FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Neon storage topology is not qualified';
 END IF;
 SELECT array_agg(oid ORDER BY oid),count(*) INTO database_ids,database_count FROM pg_database;
 IF database_count NOT BETWEEN 1 AND 16 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Neon database inventory is not qualified';
 END IF;
 -- Neon asks its pageserver for these relation sizes at the current request LSN.
 SELECT sum(size)::bigint,count(size) INTO database_bytes,measured_count
 FROM (SELECT pg_database_size(oid) AS size FROM unnest(database_ids) AS oid) sizes;
 SELECT COALESCE(sum(pg_relation_size(c.oid,fork)),0)::bigint INTO shared_bytes
 FROM pg_class c CROSS JOIN unnest(ARRAY['main','fsm','vm','init']) AS fork WHERE c.relisshared;
 IF measured_count<>database_count OR database_bytes IS NULL OR database_bytes<0 OR shared_bytes<0
    OR database_ids IS DISTINCT FROM (SELECT array_agg(oid ORDER BY oid) FROM pg_database) THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Neon branch measurement was incomplete';
 END IF;
 RETURN QUERY SELECT database_bytes+shared_bytes,actual_limit;
END $$;

CREATE FUNCTION research.refresh_neon_capacity(reviewed_limit_bytes bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE observed timestamptz; previous timestamptz; measurement record; releasable text[];
BEGIN
 IF reviewed_limit_bytes IS NULL OR reviewed_limit_bytes NOT BETWEEN 1 AND 1073741824 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Neon branch limit has not been qualified';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtext('research-query-admission'));
 observed := clock_timestamp();
 SELECT measured_at INTO previous FROM research.capacity_sample WHERE singleton;
 IF previous IS NOT NULL AND previous>=observed THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Capacity measurements must advance monotonically';
 END IF;
 -- A final transaction can commit after measurement with an older finished_at.
 -- Only runs already terminal before the measurement may lose their reservation.
 SELECT array_agg(c.run_id) INTO releasable FROM research.capacity_reservations c
 JOIN research.query_runs r ON r.id=c.run_id
 WHERE r.state IN ('complete','cancelled','failed','resource_limited') AND r.finished_at<=observed
   AND r.lease_owner IS NULL AND r.lease_until IS NULL
   AND NOT EXISTS(SELECT 1 FROM research.execution_reservations e
                  WHERE e.run_id=r.id AND e.state NOT IN ('terminal','rejected'));
 SELECT * INTO STRICT measurement FROM research.measure_neon_branch();
 IF measurement.used_bytes IS NULL OR measurement.used_bytes<0
    OR measurement.limit_bytes IS NULL OR measurement.limit_bytes<=0 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='Neon branch measurement was incomplete';
 END IF;
 INSERT INTO research.capacity_sample(singleton,used_bytes,limit_bytes,measured_at)
 VALUES(true,measurement.used_bytes,least(measurement.limit_bytes,reviewed_limit_bytes),observed)
 ON CONFLICT(singleton) DO UPDATE SET used_bytes=excluded.used_bytes,
  limit_bytes=excluded.limit_bytes,measured_at=excluded.measured_at;
 DELETE FROM research.capacity_reservations c USING research.query_runs r
 WHERE c.run_id=ANY(releasable) AND r.id=c.run_id
   AND r.state IN ('complete','cancelled','failed','resource_limited') AND r.finished_at<=observed
   AND r.lease_owner IS NULL AND r.lease_until IS NULL
   AND NOT EXISTS(SELECT 1 FROM research.execution_reservations e
                  WHERE e.run_id=r.id AND e.state NOT IN ('terminal','rejected'));
END $$;

REVOKE ALL ON FUNCTION research.measure_neon_branch(),research.refresh_neon_capacity(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.refresh_neon_capacity(bigint) TO research_api;
