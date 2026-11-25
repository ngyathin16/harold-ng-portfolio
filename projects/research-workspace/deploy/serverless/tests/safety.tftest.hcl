mock_provider "google" {}

variables {
  project_id         = "research-offline-fixture"
  billing_account    = "000000-000000-000000"
  environment        = "staging"
  app_commit         = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  recovery_principal = "user:operator@example.invalid"
  images = {
    service  = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    operator = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/operator@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
  review = {
    experiment_limit_usd = 5
    monthly_limit_usd    = 10
    provider_plan        = "OFFLINE TEST ONLY"
    database_region      = "aws-ap-southeast-1"
    quote_date           = "2026-11-25"
    evidence_reference   = "fixture: not deployment approval"
    approved             = true
  }
  secret_versions = {
    api-db      = "1", dispatcher-db = "1", search-db = "1", backup-db = "1", verify-db = "1", prune-db = "1", cleanup-db = "1", import-db = "1", restore-db = "1", session = "1",
    r2-endpoint = "1", r2-bucket = "1", r2-access = "1", r2-secret = "1", objects-endpoint = "1", objects-bucket = "1", objects-access = "1", objects-secret = "1"
  }
  r2_namespaces = { objects = "staging/objects/", backups = "staging/backups/", reports = "staging/reports/" }
}

run "bounded_private_defaults" {
  command = plan
  assert {
    condition     = google_cloud_run_v2_service.api.scaling[0].min_instance_count == 0 && google_cloud_run_v2_service.api.scaling[0].max_instance_count == 2
    error_message = "The API must scale to zero with an explicit instance ceiling."
  }
  assert {
    condition     = length(google_cloud_run_v2_service_iam_member.api) == 0 && google_cloud_run_v2_service.dispatcher.ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    error_message = "Staging defaults must not publish the API or expose the dispatcher ingress."
  }
  assert {
    condition     = google_cloud_run_v2_job.search.template[0].template[0].max_retries == 0 && google_cloud_run_v2_job.search.template[0].task_count == 1
    error_message = "Application attempt accounting cannot be multiplied by platform retries."
  }
  assert {
    condition     = google_cloud_run_v2_job.search.template[0].template[0].containers[0].args == tolist(["--bound"])
    error_message = "Search executions must bind to a reservation; arbitrary queue polling is forbidden."
  }
  assert {
    condition     = google_cloud_scheduler_job.reconcile.paused && alltrue([for schedule in google_cloud_scheduler_job.operator : schedule.paused])
    error_message = "Schedules must remain paused until their separate acceptance gates pass."
  }
  assert {
    condition     = alltrue([for grant in local.secret_access : !contains(["api", "dispatcher", "search"], grant.role) || !contains(values(local.r2_env), grant.secret)])
    error_message = "Runtime API, dispatcher and search identities must not receive R2 credentials."
  }
  assert {
    condition     = local.common_env.RESEARCH_CLIENT_IP_MODE == "shared" && local.common_env.RESEARCH_RESULT_RESERVATION_BYTES == "2147483648"
    error_message = "All serverless processes need the qualified identity mode and measured result reservation bound."
  }
  assert {
    condition     = local.secret_access["restore-source-lock"].role == "restore" && local.secret_access["restore-source-lock"].secret == "backup-db" && contains([for env in google_cloud_run_v2_job.operator["restore"].template[0].template[0].containers[0].env : env.name], "RESEARCH_BACKUP_DATABASE_URL")
    error_message = "Restore must hold the source backup-reader lock while using its distinct target database identity."
  }
}

run "refuse_unreviewed_spending" {
  command = plan
  variables {
    review = {
      experiment_limit_usd = 0, monthly_limit_usd = 0, provider_plan = "pending", database_region = "pending",
      quote_date           = "2026-11-25", evidence_reference = "pending", approved = false
    }
  }
  expect_failures = [var.review]
}

run "refuse_unverified_schedules" {
  command = plan
  variables { enable_operator_schedules = true }
  expect_failures = [google_cloud_scheduler_job.operator]
}

run "refuse_mutable_images" {
  command = plan
  variables { images = { service = "example.invalid/service:latest", operator = "example.invalid/operator:latest" } }
  expect_failures = [var.images]
}

run "refuse_cross_environment_namespaces" {
  command = plan
  variables { environment = "production" }
  expect_failures = [var.r2_namespaces]
}

run "refuse_memory_backed_staging_overcommit" {
  command = plan
  variables { operator_staging_bytes = 4294967296 }
  expect_failures = [var.operator_staging_bytes]
}
