# Cloud Run configuration

This module creates no database and carries no secret values. It describes the API, private dispatcher, allowlisted search Job, six operator Jobs, identities, secret containers/grants, registry, Tasks queue, private dispatch-hint bucket, paused schedules, and alerts. It requires the reviewed spending/provider decision. See the [operator runbook](../../../../docs/RESEARCH-SERVERLESS.md) and [cost worksheet](../../../../docs/RESEARCH-SERVERLESS-COST.md).

Local validation, without credentials or provisioning:

```text
terraform init -backend=false -input=false
terraform fmt -check -recursive
terraform validate
terraform test
```

The test provider is mocked. Test budgets, identities and image digests are fixtures, never deployment inputs. Provider schema validation does not verify Google IAM permissions, region quotas, external endpoint reachability or billing filters. Repeat those in the authorized isolated rehearsal.

Create private variables from `staging.example.tfvars`; the example fails approval/image checks intentionally. Use distinct persistent Terraform states/workspaces per environment. An existing managed remote state backend can be configured separately; protect it like an administrative artifact and record its cost. Never share a production state with staging or delete state to retry a deployment.

Set `google_alert_budget` to the actual billing account currency, exact alert amount and reviewed USD equivalent, with dated conversion evidence in `review`. The owner account uses HKD; the sample deliberately has zero placeholders until reviewed. Leaving the object null uses USD and is suitable only for USD billing accounts. Its equivalent shares the under-US$5 total ceiling across all providers; alerts are not a spend cap. The separate account-wide HK$15 early warning remains an additional notification, not additional spending authority.

After authorization and the prerequisites in the runbook:

```text
terraform workspace select staging
terraform plan -var-file=ABSOLUTE_PRIVATE_TFVARS -out=ABSOLUTE_PRIVATE_PLAN
terraform show ABSOLUTE_PRIVATE_PLAN
terraform apply ABSOLUTE_PRIVATE_PLAN
terraform plan -var-file=ABSOLUTE_PRIVATE_TFVARS -detailed-exitcode
```

Create the workspace once with `terraform workspace new staging` if it does not exist. The last command must return 0 for a no-op repeat. An exit code of 2 means a diff requiring review. Do not use `-auto-approve`, arbitrary `-target` on ordinary releases, or destroy/recreate to recover state.

For first bootstrap only, build and inspect images locally to obtain the reviewed OCI manifest digests, then apply a reviewed targeted plan for project APIs, service identities, secret containers and the registry. Upload the same image manifests and secret versions, then review/apply the full plan. Secret creation is distinct from secret version payload upload, so an ordinary second apply cannot reset credentials. Never substitute invented digests merely to pass validation. A previously existing approved registry also avoids this bootstrap ordering.

Database migration is an explicit operator step. It is never triggered by Terraform or API startup. Pin the same source commit in image labels and `app_commit`, and reference numeric Secret Manager versions. A rollback changes the reviewed image digests against the compatible current schema; it does not restore old accepted-work state.

The dispatch bucket is private, regional and separate per environment. API may create hints; dispatcher may list/delete them; the independent recovery principal may create/list. Workers have no bucket access. Versioning, soft delete and automatic expiry are disabled; `prevent_destroy` and `force_destroy=false` protect live hints. PostgreSQL remains authoritative, but deleting a pending hint can hide work from idle reconciliation. Keep dispatch paused during first rollout or restore, set the bucket name from `dispatch_marker_bucket`, repair markers using `python -m ops.serverless_control repair-markers`, then use the guarded `resume` command. See the runbook for direct database and short-lived operator authentication.

Operator `verify` selects the newest complete set when no explicit backup ID is provided. Import/restore need additional immutable identifiers and bounds supplied as Job overrides by the independently authenticated recovery principal. Restore uses a different target database secret and object prefix; it cannot be scheduled by the scheduler service identity. The default module does not grant anonymous API access or enable schedules. Public launch and unattended operators require their separate evidence/decisions in the runbook.

Operator secret bindings are part of the Job definition, never execution overrides: `RESEARCH_OPERATOR_DATABASE_URL` references the Job's numeric database secret version. For restore this is `restore-db`, which must name an isolated target; only the restore identity also receives `backup-db` as `RESEARCH_BACKUP_DATABASE_URL` to hold the source retention lock. Populate `backup-db`, `verify-db`, and `prune-db` with the source backup-reader login; `import-db` and `cleanup-db` with the importer login. Do not populate a runtime secret with the database owner connection.

Supply the following noncredential execution overrides using the authenticated Cloud Run Jobs v2 `:run` API (`overrides.containerOverrides[0].env`, each entry containing `name` and `value`) or `gcloud run jobs execute --update-env-vars`. Keep the reviewed JSON request file private; its baseline describes the provider target. The recovery principal has Job-scoped `roles/run.jobsExecutorWithOverrides`; the Scheduler identity cannot override jobs or invoke restore/import.

| Job | Required overrides | Optional overrides |
| --- | --- | --- |
| import | `RESEARCH_IMPORT_BUNDLE_KEY` immutable R2 directory prefix; `RESEARCH_IMPORT_RESTORED_BYTES` positive decompression reservation; `RESEARCH_IMPORT_VERIFY_BYTES` positive logical-hash scratch reservation | None |
| restore | `RESEARCH_BACKUP_ID` exact retained set; `RESEARCH_RESTORE_BASELINE_JSON` serialized reviewed target baseline; `RESEARCH_RESTORE_OBJECT_PREFIX` distinct environment-qualified destination | None |
| verify | None; defaults to newest complete set | `RESEARCH_BACKUP_ID` for an exact set |

For example, after replacing all placeholders with reviewed values:

```text
gcloud run jobs execute research-staging-import --project=PROJECT --region=asia-southeast1 --update-env-vars=RESEARCH_IMPORT_BUNDLE_KEY=BUNDLE_PREFIX,RESEARCH_IMPORT_RESTORED_BYTES=DECOMPRESSED_BOUND,RESEARCH_IMPORT_VERIFY_BYTES=HASH_SCRATCH_BOUND
```

For restore, use a JSON request file to preserve the baseline's JSON escaping rather than embedding a complex value in shell text. Invoke `POST https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/jobs/research-staging-restore:run` with an OAuth bearer token acquired through the operator's authenticated client. Construct the file with a JSON serializer; never interpolate DSNs or R2 credentials. The API request shape is:

```json
{
  "overrides": {
    "containerOverrides": [{
      "env": [
        {"name": "RESEARCH_BACKUP_ID", "value": "REPLACE_VERIFIED_BACKUP_ID"},
        {"name": "RESEARCH_RESTORE_BASELINE_JSON", "value": "REPLACE_SERIALIZED_REVIEWED_BASELINE"},
        {"name": "RESEARCH_RESTORE_OBJECT_PREFIX", "value": "staging/restore-drill-IDENTITY/"}
      ]
    }]
  }
}
```

Successful API acceptance is not Job completion. Poll the returned operation/execution and retain its sanitized operator report. Full operator contracts and restore assertions are in [ops/serverless-README.md](../../ops/serverless-README.md).
