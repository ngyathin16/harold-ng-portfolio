mock_provider "google" {}

variables {
  project_id         = "research-offline-fixture"
  billing_account    = "000000-000000-000000"
  environment        = "staging"
  app_commit         = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  recovery_principal = "user:operator@example.invalid"
  budget_window_id   = "offline-reviewed-fixture"
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
    r2-endpoint = "1", r2-bucket = "1", r2-access = "1", r2-secret = "1", objects-endpoint = "1", objects-bucket = "1", objects-access = "1", objects-secret = "1",
    result-api  = "2", result-worker = "3", result-operator = "4"
  }
  r2_namespaces = { objects = "staging/objects/", backups = "staging/backups/", reports = "staging/reports/" }
  result_storage = {
    bucket            = "private-results-fixture", prefix = "staging/results/qualified-fixture/",
    capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108864, headroom_bytes = 134217728
  }
}

run "refuse_unmetered_object_results" {
  command = plan
  variables { budget_window_id = "" }
  expect_failures = [var.result_storage]
}

run "qualified_neon_capacity_and_budget_connections" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/qualified-fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 268435456,
      headroom_bytes    = 134217728, capacity_measurement_mode = "neon_branch", capacity_limit_bytes = 1073741824
    }
  }
  assert {
    condition = (
      local.common_env.RESEARCH_CAPACITY_MEASUREMENT_MODE == "neon_branch" &&
      local.common_env.RESEARCH_CAPACITY_LIMIT_BYTES == "1073741824" &&
      one([for env in google_cloud_run_v2_service.api.template[0].containers[0].env : env.value if env.name == "RESEARCH_POOL_SIZE"]) == "3" &&
      alltrue([for container in concat(
        [for service in [google_cloud_run_v2_service.api, google_cloud_run_v2_service.dispatcher] : service.template[0].containers[0]],
        [for job in concat([google_cloud_run_v2_job.search], values(google_cloud_run_v2_job.operator)) : job.template[0].template[0].containers[0]]) :
      one([for env in container.env : env.value if env.name == "RESEARCH_BUDGET_WINDOW_ID"]) == var.budget_window_id])
    )
    error_message = "Qualified capacity and resource windows must reach each runtime while API connections leave room for the independent accounting pool."
  }
}

run "refuse_neon_limit_above_reviewed_free_plan" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/qualified-fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 268435456,
      headroom_bytes    = 134217728, capacity_measurement_mode = "neon_branch", capacity_limit_bytes = 1073741825
    }
  }
  expect_failures = [var.result_storage]
}

run "object_credentials_are_separate_and_legacy_containers_survive" {
  command = plan
  assert {
    condition = (
      length(google_secret_manager_secret.secret) == 21 &&
      alltrue([for name in values(local.r2_env) : contains(keys(google_secret_manager_secret.secret), name)]) &&
      length(local.secret_access) == 19 &&
      alltrue([for grant in local.secret_access : !contains(values(local.r2_env), grant.secret)]) &&
      toset([for grant in local.secret_access : grant.role if grant.secret == "result-api"]) == toset(["api"]) &&
      toset([for grant in local.secret_access : grant.role if grant.secret == "result-worker"]) == toset(["search"]) &&
      toset([for grant in local.secret_access : grant.role if grant.secret == "result-operator"]) == toset(["backup", "verify", "prune", "cleanup", "import", "restore"])
    )
    error_message = "Keep all legacy secret containers, replace their runtime grants, and scope each new bundle to its intended identities."
  }
  assert {
    condition = (
      one([for env in google_cloud_run_v2_service.api.template[0].containers[0].env : env.value_source[0].secret_key_ref[0].secret if env.name == "RESEARCH_RESULT_CREDENTIALS_JSON"]) == "research-staging-result-api" &&
      one([for env in google_cloud_run_v2_service.api.template[0].containers[0].env : env.value_source[0].secret_key_ref[0].version if env.name == "RESEARCH_RESULT_CREDENTIALS_JSON"]) == "2" &&
      one([for env in google_cloud_run_v2_job.search.template[0].template[0].containers[0].env : env.value_source[0].secret_key_ref[0].secret if env.name == "RESEARCH_RESULT_CREDENTIALS_JSON"]) == "research-staging-result-worker" &&
      one([for env in google_cloud_run_v2_job.search.template[0].template[0].containers[0].env : env.value_source[0].secret_key_ref[0].version if env.name == "RESEARCH_RESULT_CREDENTIALS_JSON"]) == "3"
    )
    error_message = "API and search must reference their distinct immutable credential versions."
  }
  assert {
    condition = alltrue([for job in google_cloud_run_v2_job.operator :
      one([for env in job.template[0].template[0].containers[0].env : env.value_source[0].secret_key_ref[0].secret if env.name == "RESEARCH_RESULT_CREDENTIALS_JSON"]) == "research-staging-result-operator" &&
      one([for env in job.template[0].template[0].containers[0].env : env.value_source[0].secret_key_ref[0].version if env.name == "RESEARCH_RESULT_CREDENTIALS_JSON"]) == "4" &&
      alltrue([for env in job.template[0].template[0].containers[0].env : !contains(keys(local.r2_env), env.name)])
    ])
    error_message = "Every operator must use only the new bundle for R2 credentials, with no legacy R2 environment bindings."
  }
  assert {
    condition = alltrue([
      for container in concat([google_cloud_run_v2_service.api.template[0].containers[0]],
      [for job in concat([google_cloud_run_v2_job.search], values(google_cloud_run_v2_job.operator)) : job.template[0].template[0].containers[0]]) :
      one([for env in container.env : env.value if env.name == "RESEARCH_RESULT_STORAGE_MODE"]) == "objects" &&
      one([for env in container.env : env.value if env.name == "RESEARCH_RESULT_BUCKET"]) == var.result_storage.bucket &&
      one([for env in container.env : env.value if env.name == "RESEARCH_RESULT_PREFIX"]) == var.result_storage.prefix &&
      one([for env in container.env : env.value if env.name == "RESEARCH_RESULT_RESERVATION_BYTES"]) == "67108864" &&
      one([for env in container.env : env.value if env.name == "RESEARCH_DATABASE_HEADROOM_BYTES"]) == "134217728"
    ])
    error_message = "API, search and operators must share the reviewed result namespace and metadata capacity bounds."
  }
  assert {
    condition = (
      alltrue([for grant in local.secret_access : grant.role != "dispatcher" || grant.secret == "dispatcher-db"]) &&
      alltrue([for env in google_cloud_run_v2_service.dispatcher.template[0].containers[0].env :
        !contains(concat(keys(local.r2_env), ["RESEARCH_RESULT_STORAGE_MODE", "RESEARCH_RESULT_CREDENTIALS_JSON", "RESEARCH_RESULT_BUCKET", "RESEARCH_RESULT_PREFIX"]), env.name)
      ]) &&
      one([for env in google_cloud_run_v2_service.dispatcher.template[0].containers[0].env : env.value if env.name == "RESEARCH_RESULT_RESERVATION_BYTES"]) == "67108864" &&
      one([for env in google_cloud_run_v2_service.dispatcher.template[0].containers[0].env : env.value if env.name == "RESEARCH_DATABASE_HEADROOM_BYTES"]) == "134217728"
    )
    error_message = "Dispatcher needs the new admission bounds but must receive no R2 credentials, namespace, or object mode."
  }
  assert {
    condition = (
      google_cloud_scheduler_job.reconcile.paused && alltrue([for schedule in google_cloud_scheduler_job.operator : schedule.paused]) &&
      length(google_cloud_run_v2_service_iam_member.api) == 0 &&
      google_cloud_run_v2_job.search.template[0].template[0].max_retries == 0 &&
      var.review.monthly_limit_usd == 4.99
    )
    error_message = "Result storage must not publish the API, enable schedules, add retries or increase spending authority."
  }
}

run "null_preserves_existing_secret_grants_and_capacity" {
  command = plan
  variables { result_storage = null }
  assert {
    condition = (
      length(google_secret_manager_secret.secret) == 18 && length(local.secret_access) == 59 &&
      alltrue([for name in ["result-api", "result-worker", "result-operator"] : !contains(keys(google_secret_manager_secret.secret), name)]) &&
      local.common_env.RESEARCH_RESULT_RESERVATION_BYTES == "2147483648" &&
      local.common_env.RESEARCH_DATABASE_HEADROOM_BYTES == "1073741824" &&
      alltrue([for job in google_cloud_run_v2_job.operator :
        alltrue([for name in keys(local.r2_env) : contains([for env in job.template[0].template[0].containers[0].env : env.name], name)])
      ]) &&
      alltrue([
        for container in concat(
          [for service in [google_cloud_run_v2_service.api, google_cloud_run_v2_service.dispatcher] : service.template[0].containers[0]],
          [for job in concat([google_cloud_run_v2_job.search], values(google_cloud_run_v2_job.operator)) : job.template[0].template[0].containers[0]]
          ) : alltrue([for env in container.env :
            !contains(["RESEARCH_RESULT_STORAGE_MODE", "RESEARCH_RESULT_CREDENTIALS_JSON", "RESEARCH_RESULT_BUCKET", "RESEARCH_RESULT_PREFIX"], env.name)
        ])
      ])
    )
    error_message = "Null must retain the existing 18-container/59-grant plan and its original capacity settings."
  }
}

run "object_mode_needs_only_active_secret_versions" {
  command = plan
  variables {
    secret_versions = {
      api-db     = "1", dispatcher-db = "1", search-db = "1", backup-db = "1", verify-db = "1", prune-db = "1", cleanup-db = "1", import-db = "1", restore-db = "1", session = "1",
      result-api = "2", result-worker = "3", result-operator = "4"
    }
  }
  assert {
    condition     = length(var.secret_versions) == 13 && length(google_secret_manager_secret.secret) == 21
    error_message = "Legacy containers must remain without requiring their disabled versions in the active deployment."
  }
}

run "production_results_are_separate" {
  command = plan
  variables {
    environment   = "production"
    r2_namespaces = { objects = "production/objects/", backups = "production/backups/", reports = "production/reports/" }
    result_storage = {
      bucket            = "private-results-fixture", prefix = "production/results/qualified-fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108864, headroom_bytes = 134217728
    }
  }
  assert {
    condition     = local.result_env.RESEARCH_RESULT_PREFIX == "production/results/qualified-fixture/"
    error_message = "Production results must use their own reviewed namespace."
  }
}

run "refuse_cross_environment_results" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "production/results/fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108864, headroom_bytes = 134217728
    }
  }
  expect_failures = [var.result_storage]
}

run "refuse_result_path_traversal" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/../backups/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108864, headroom_bytes = 134217728
    }
  }
  expect_failures = [var.result_storage]
}

run "refuse_results_inside_a_source_namespace" {
  command = plan
  variables { r2_namespaces = { objects = "staging/results/", backups = "staging/backups/", reports = "staging/reports/" } }
  expect_failures = [var.result_storage]
}

run "refuse_source_namespace_inside_results" {
  command = plan
  variables { r2_namespaces = { objects = "staging/objects/", backups = "staging/results/qualified-fixture/backups/", reports = "staging/reports/" } }
  expect_failures = [var.result_storage]
}

run "refuse_unqualified_capacity" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/fixture/",
      capacity_evidence = "   ", reservation_bytes = 67108864, headroom_bytes = 134217728
    }
  }
  expect_failures = [var.result_storage]
}

run "refuse_small_result_reservation" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108863, headroom_bytes = 134217728
    }
  }
  expect_failures = [var.result_storage]
}

run "refuse_small_headroom" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108864, headroom_bytes = 134217727
    }
  }
  expect_failures = [var.result_storage]
}

run "refuse_fractional_capacity" {
  command = plan
  variables {
    result_storage = {
      bucket            = "private-results-fixture", prefix = "staging/results/fixture/",
      capacity_evidence = "fixture: not deployment qualification", reservation_bytes = 67108864.5, headroom_bytes = 134217728.5
    }
  }
  expect_failures = [var.result_storage]
}

run "refuse_missing_result_versions" {
  command = plan
  variables {
    secret_versions = {
      api-db = "1", dispatcher-db = "1", search-db = "1", backup-db = "1", verify-db = "1", prune-db = "1", cleanup-db = "1", import-db = "1", restore-db = "1", session = "1"
    }
  }
  expect_failures = [var.result_storage]
}
