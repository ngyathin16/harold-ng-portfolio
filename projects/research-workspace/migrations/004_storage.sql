-- Apply on Supabase after the three relational migrations. Bucket remains private.
BEGIN;
INSERT INTO storage.buckets(id,name,public) VALUES('research-artifacts','research-artifacts',false)
ON CONFLICT(id) DO UPDATE SET public=false;
CREATE POLICY research_published_downloads ON storage.objects FOR SELECT TO anon,authenticated
USING(bucket_id='research-artifacts' AND EXISTS(
 SELECT 1 FROM research.artifacts a JOIN research.publications p ON p.id=a.publication_id
 WHERE a.storage_key=storage.objects.name AND p.status='published'
));
COMMIT;
