-- Retain recovery inventory while removing visitor access to stored files.
BEGIN;
REVOKE SELECT ON research.artifacts FROM anon,authenticated;
DROP POLICY IF EXISTS published_read ON research.artifacts;
UPDATE research.datasets SET capabilities=capabilities-'downloads'
WHERE capabilities ? 'downloads';
DO $$ BEGIN
 IF to_regclass('storage.objects') IS NOT NULL THEN
  EXECUTE 'DROP POLICY IF EXISTS research_published_downloads ON storage.objects';
 END IF;
END $$;
COMMIT;
