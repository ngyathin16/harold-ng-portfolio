# This file cannot authorize provisioning. Replace every pending value after review.
project_id         = "REPLACE_PROJECT"
billing_account    = "REPLACE_BILLING_ACCOUNT"
environment        = "staging"
region             = "asia-southeast1"
app_commit         = "REPLACE_REVIEWED_COMMIT"
recovery_principal = "user:REPLACE_OPERATOR"
images = {
  service  = "REPLACE_REGISTRY_SERVICE_DIGEST"
  operator = "REPLACE_REGISTRY_OPERATOR_DIGEST"
}
review = {
  experiment_limit_usd = 0
  monthly_limit_usd    = 4.99
  provider_plan        = "pending exact provider qualification"
  database_region      = "pending measured region pair"
  quote_date           = "2026-11-25"
  evidence_reference   = "pending"
  approved             = false
}
# Match the billing account currency. Replace both zeros using the reviewed
# conversion/quote in review.evidence_reference on review.quote_date.
# This alert shares the USD 4.99 ceiling; it does not add spending authority.
google_alert_budget = {
  currency_code  = "HKD"
  amount         = 0
  usd_equivalent = 0
}
secret_versions = {
  api-db      = "1", dispatcher-db = "1", search-db = "1", backup-db = "1", verify-db = "1", prune-db = "1", cleanup-db = "1", import-db = "1", restore-db = "1", session = "1",
  r2-endpoint = "1", r2-bucket = "1", r2-access = "1", r2-secret = "1", objects-endpoint = "1", objects-bucket = "1", objects-access = "1", objects-secret = "1"
}
r2_namespaces             = { objects = "staging/objects/", backups = "staging/backups/", reports = "staging/reports/" }
notification_channels     = []
rehearsal_invokers        = []
public_api                = false
enable_reconciliation     = false
enable_operator_schedules = false
