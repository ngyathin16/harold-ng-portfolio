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
    experiment_limit_usd = 1
    monthly_limit_usd    = 4.99
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
  assert {
    condition     = google_billing_budget.monthly.amount[0].specified_amount[0].currency_code == "USD" && google_billing_budget.monthly.amount[0].specified_amount[0].units == "4" && google_billing_budget.monthly.amount[0].specified_amount[0].nanos == 990000000
    error_message = "The USD 4.99 alert must retain its cents instead of rounding up to USD 5."
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

run "dispatch_markers_are_private_durable_and_scoped" {
  command = plan
  override_resource {
    target          = google_service_account.identity["api"]
    override_during = plan
    values          = { email = "research-staging-api@research-offline-fixture.iam.gserviceaccount.com" }
  }
  override_resource {
    target          = google_service_account.identity["dispatcher"]
    override_during = plan
    values          = { email = "research-staging-dispatcher@research-offline-fixture.iam.gserviceaccount.com" }
  }
  override_resource {
    target          = google_project_iam_custom_role.dispatch_markers["api"]
    override_during = plan
    values          = { name = "projects/research-offline-fixture/roles/research_staging_marker_api" }
  }
  override_resource {
    target          = google_project_iam_custom_role.dispatch_markers["dispatcher"]
    override_during = plan
    values          = { name = "projects/research-offline-fixture/roles/research_staging_marker_dispatcher" }
  }
  override_resource {
    target          = google_project_iam_custom_role.dispatch_markers["recovery"]
    override_during = plan
    values          = { name = "projects/research-offline-fixture/roles/research_staging_marker_recovery" }
  }
  assert {
    condition = (
      google_storage_bucket.dispatch_markers.name == "research-offline-fixture-research-staging-dispatch" &&
      google_storage_bucket.dispatch_markers.location == upper(var.region) &&
      google_storage_bucket.dispatch_markers.storage_class == "STANDARD" &&
      contains(keys(google_project_service.api), "storage.googleapis.com")
    )
    error_message = "Dispatch markers need a dedicated environment bucket colocated with the services."
  }
  assert {
    condition = (
      google_storage_bucket.dispatch_markers.uniform_bucket_level_access &&
      google_storage_bucket.dispatch_markers.public_access_prevention == "enforced" &&
      !google_storage_bucket.dispatch_markers.force_destroy &&
      !google_storage_bucket.dispatch_markers.versioning[0].enabled &&
      google_storage_bucket.dispatch_markers.soft_delete_policy[0].retention_duration_seconds == 0 &&
      length(google_storage_bucket.dispatch_markers.lifecycle_rule) == 0
    )
    error_message = "Markers must stay private and persist until reconciliation, without expiry, retained deleted copies, or forced bucket deletion."
  }
  assert {
    condition = (
      google_project_iam_custom_role.dispatch_markers["api"].permissions == toset(["storage.objects.create"]) &&
      google_project_iam_custom_role.dispatch_markers["dispatcher"].permissions == toset(["storage.objects.list", "storage.objects.delete"]) &&
      google_project_iam_custom_role.dispatch_markers["recovery"].permissions == toset(["storage.objects.create", "storage.objects.list"])
    )
    error_message = "API may only create markers; dispatcher may only list/delete; recovery may only create/list."
  }
  assert {
    condition = (
      length(google_storage_bucket_iam_member.dispatch_markers) == 3 &&
      alltrue([for key, grant in google_storage_bucket_iam_member.dispatch_markers : grant.bucket == google_storage_bucket.dispatch_markers.name && grant.role == google_project_iam_custom_role.dispatch_markers[key].name]) &&
      google_storage_bucket_iam_member.dispatch_markers["api"].member == "serviceAccount:${google_service_account.identity["api"].email}" &&
      google_storage_bucket_iam_member.dispatch_markers["dispatcher"].member == "serviceAccount:${google_service_account.identity["dispatcher"].email}" &&
      google_storage_bucket_iam_member.dispatch_markers["recovery"].member == var.recovery_principal
    )
    error_message = "Marker access must be bucket scoped to the API, dispatcher, and independent recovery principal only."
  }
  assert {
    condition = alltrue([
      for service in [google_cloud_run_v2_service.api, google_cloud_run_v2_service.dispatcher] :
      one([for env in service.template[0].containers[0].env : env.value if env.name == "RESEARCH_DISPATCH_MARKER_BUCKET"]) == google_storage_bucket.dispatch_markers.name &&
      one([for env in service.template[0].containers[0].env : env.value if env.name == "RESEARCH_DISPATCH_MARKER_PREFIX"]) == "dispatch/"
    ])
    error_message = "API and dispatcher must share the environment's marker bucket and prefix."
  }
  assert {
    condition = alltrue([
      for job in concat([google_cloud_run_v2_job.search], values(google_cloud_run_v2_job.operator)) :
      alltrue([for env in job.template[0].template[0].containers[0].env : !startswith(env.name, "RESEARCH_DISPATCH_MARKER_")])
    ])
    error_message = "Workers and operator jobs must not inherit marker storage configuration or access."
  }
}

run "production_markers_are_separate" {
  command = plan
  variables {
    environment   = "production"
    r2_namespaces = { objects = "production/objects/", backups = "production/backups/", reports = "production/reports/" }
  }
  assert {
    condition     = google_storage_bucket.dispatch_markers.name == "research-offline-fixture-research-production-dispatch"
    error_message = "Production dispatch markers must never share the staging bucket."
  }
}

run "refuse_runtime_marker_recovery_principal" {
  command = plan
  variables { recovery_principal = "serviceAccount:research-staging-search@research-offline-fixture.iam.gserviceaccount.com" }
  expect_failures = [google_storage_bucket_iam_member.dispatch_markers["recovery"]]
}

run "refuse_public_marker_recovery_principal" {
  command = plan
  variables { recovery_principal = "allUsers" }
  expect_failures = [google_storage_bucket_iam_member.dispatch_markers["recovery"]]
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

run "refuse_exact_five_dollar_ceiling" {
  command = plan
  variables {
    review = {
      experiment_limit_usd = 1, monthly_limit_usd = 5, provider_plan = "OFFLINE TEST ONLY", database_region = "aws-ap-southeast-1",
      quote_date           = "2026-11-25", evidence_reference = "fixture: not deployment approval", approved = true
    }
  }
  expect_failures = [var.review]
}

run "refuse_over_five_dollar_ceiling" {
  command = plan
  variables {
    review = {
      experiment_limit_usd = 1, monthly_limit_usd = 10, provider_plan = "OFFLINE TEST ONLY", database_region = "aws-ap-southeast-1",
      quote_date           = "2026-11-25", evidence_reference = "fixture: not deployment approval", approved = true
    }
  }
  expect_failures = [var.review]
}

run "refuse_experiment_outside_monthly_envelope" {
  command = plan
  variables {
    review = {
      experiment_limit_usd = 4.99, monthly_limit_usd = 4.50, provider_plan = "OFFLINE TEST ONLY", database_region = "aws-ap-southeast-1",
      quote_date           = "2026-11-25", evidence_reference = "fixture: not deployment approval", approved = true
    }
  }
  expect_failures = [var.review]
}

run "refuse_zero_budget_staging_provisioning" {
  command = plan
  variables {
    review = {
      experiment_limit_usd = 0, monthly_limit_usd = 4.99, provider_plan = "OFFLINE TEST ONLY", database_region = "aws-ap-southeast-1",
      quote_date           = "2026-11-25", evidence_reference = "fixture: not deployment approval", approved = true
    }
  }
  expect_failures = [google_project_service.api]
}

run "hkd_alert_stays_inside_usd_allowance" {
  command = plan
  variables {
    google_alert_budget = { currency_code = "HKD", amount = 15, usd_equivalent = 2 }
  }
  assert {
    condition = (
      google_billing_budget.monthly.amount[0].specified_amount[0].currency_code == "HKD" &&
      google_billing_budget.monthly.amount[0].specified_amount[0].units == "15" &&
      google_billing_budget.monthly.amount[0].specified_amount[0].nanos == 0 &&
      var.review.monthly_limit_usd == 4.99 &&
      local.google_alert_budget.usd_equivalent == 2
    )
    error_message = "The fixture must create a HKD 15 alert whose reviewed USD 2 equivalent shares the existing USD 4.99 ceiling."
  }
}

run "hkd_alert_preserves_cents" {
  command = plan
  variables {
    google_alert_budget = { currency_code = "HKD", amount = 15.37, usd_equivalent = 2 }
  }
  assert {
    condition = (
      google_billing_budget.monthly.amount[0].specified_amount[0].currency_code == "HKD" &&
      google_billing_budget.monthly.amount[0].specified_amount[0].units == "15" &&
      google_billing_budget.monthly.amount[0].specified_amount[0].nanos == 370000000
    )
    error_message = "Non-USD alert amounts must retain their actual currency and cents."
  }
}

run "refuse_alert_equivalent_at_five_dollars" {
  command = plan
  variables { google_alert_budget = { currency_code = "HKD", amount = 15, usd_equivalent = 5 } }
  expect_failures = [var.google_alert_budget]
}

run "refuse_alert_equivalent_above_monthly_allowance" {
  command = plan
  variables {
    review = {
      experiment_limit_usd = 1, monthly_limit_usd = 3, provider_plan = "OFFLINE TEST ONLY", database_region = "aws-ap-southeast-1",
      quote_date           = "2026-11-25", evidence_reference = "fixture: not deployment approval", approved = true
    }
    google_alert_budget = { currency_code = "HKD", amount = 15, usd_equivalent = 4 }
  }
  expect_failures = [var.google_alert_budget]
}

run "refuse_zero_alert_equivalent" {
  command = plan
  variables { google_alert_budget = { currency_code = "HKD", amount = 15, usd_equivalent = 0 } }
  expect_failures = [var.google_alert_budget]
}

run "refuse_zero_alert_amount" {
  command = plan
  variables { google_alert_budget = { currency_code = "HKD", amount = 0, usd_equivalent = 2 } }
  expect_failures = [var.google_alert_budget]
}

run "refuse_fractional_alert_cents" {
  command = plan
  variables { google_alert_budget = { currency_code = "HKD", amount = 15.001, usd_equivalent = 2 } }
  expect_failures = [var.google_alert_budget]
}

run "refuse_unsupported_alert_currency" {
  command = plan
  variables { google_alert_budget = { currency_code = "XYZ", amount = 15, usd_equivalent = 2 } }
  expect_failures = [var.google_alert_budget]
}

run "refuse_different_usd_alert_equivalent" {
  command = plan
  variables { google_alert_budget = { currency_code = "USD", amount = 4, usd_equivalent = 2 } }
  expect_failures = [var.google_alert_budget]
}
