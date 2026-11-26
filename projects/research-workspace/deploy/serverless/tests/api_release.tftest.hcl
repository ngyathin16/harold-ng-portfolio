mock_provider "google" {}

override_resource {
  target          = google_service_account.identity["dispatcher"]
  override_during = plan
  values          = { email = "research-staging-dispatcher@research-offline-fixture.iam.gserviceaccount.com" }
}

variables {
  project_id         = "research-offline-fixture"
  billing_account    = "000000-000000-000000"
  environment        = "staging"
  app_commit         = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  recovery_principal = "user:operator@example.invalid"
  images = {
    service  = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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

run "null_api_override_preserves_base_release" {
  command = plan
  assert {
    condition = (
      google_cloud_run_v2_service.api.template[0].containers[0].image == var.images.service &&
      one([for env in google_cloud_run_v2_service.api.template[0].containers[0].env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.app_commit
    )
    error_message = "Null API release must preserve its base image and source commit."
  }
}

run "api_override_preserves_dispatcher_and_every_job" {
  command = plan
  variables {
    api_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "cccccccccccccccccccccccccccccccccccccccc"
    }
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  assert {
    condition = (
      google_cloud_run_v2_service.api.template[0].containers[0].image == var.api_release.image &&
      one([for env in google_cloud_run_v2_service.api.template[0].containers[0].env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.api_release.app_commit &&
      google_cloud_run_v2_service.dispatcher.template[0].containers[0].image == var.dispatcher_release.image &&
      one([for env in google_cloud_run_v2_service.dispatcher.template[0].containers[0].env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.dispatcher_release.app_commit
    )
    error_message = "API and dispatcher overrides must remain separate atomic image/commit pairs."
  }
  assert {
    condition = (
      google_cloud_run_v2_job.search.template[0].template[0].containers[0].image == var.images.service &&
      alltrue([for job in values(google_cloud_run_v2_job.operator) : job.template[0].template[0].containers[0].image == var.images.operator]) &&
      alltrue([for container in concat([google_cloud_run_v2_job.search.template[0].template[0].containers[0]],
        [for job in values(google_cloud_run_v2_job.operator) : job.template[0].template[0].containers[0]]) :
      one([for env in container.env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.app_commit]) &&
      google_cloud_run_v2_job.search.template[0].template[0].max_retries == 0 &&
      google_cloud_run_v2_job.search.template[0].template[0].timeout == "300s" &&
      google_cloud_run_v2_job.search.template[0].template[0].containers[0].args == tolist(["--bound"])
    )
    error_message = "The API correction must preserve the reserved search Job and all six operator releases and bounds."
  }
  assert {
    condition = (
      google_cloud_run_v2_service.api.template[0].timeout == "15s" &&
      google_cloud_run_v2_service.api.template[0].max_instance_request_concurrency == 16 &&
      google_cloud_run_v2_service.api.scaling[0].max_instance_count == 2 &&
      google_cloud_run_v2_service.api.template[0].containers[0].resources[0].limits == tomap({ cpu = "1", memory = "512Mi" }) &&
      google_cloud_run_v2_service.api.template[0].containers[0].resources[0].startup_cpu_boost == false &&
      google_cloud_run_v2_service.api.template[0].containers[0].startup_probe[0].initial_delay_seconds == 0 &&
      google_cloud_run_v2_service.api.template[0].containers[0].startup_probe[0].timeout_seconds == 1 &&
      google_cloud_run_v2_service.api.template[0].containers[0].startup_probe[0].period_seconds == 10 &&
      google_cloud_run_v2_service.api.template[0].containers[0].startup_probe[0].failure_threshold == 3 &&
      google_cloud_run_v2_service.api.template[0].containers[0].startup_probe[0].http_get[0].path == "/live" &&
      length(google_cloud_run_v2_service_iam_member.api) == 0 && google_cloud_scheduler_job.reconcile.paused
    )
    error_message = "API override must retain bounded private configuration, explicit30-second startup probe and paused schedules."
  }
}

run "reject_partial_commit" {
  command = plan
  variables {
    api_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = null
    }
  }
  expect_failures = [var.api_release]
}

run "reject_partial_image" {
  command = plan
  variables {
    api_release = { image = null, app_commit = "dddddddddddddddddddddddddddddddddddddddd" }
  }
  expect_failures = [var.api_release]
}

run "reject_short_commit" {
  command = plan
  variables {
    api_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "ddddddd"
    }
  }
  expect_failures = [var.api_release]
}

run "reject_mutable_image" {
  command = plan
  variables {
    api_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service:latest"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  expect_failures = [var.api_release]
}

run "reject_other_repository" {
  command = plan
  variables {
    api_release = {
      image      = "asia-southeast1-docker.pkg.dev/another-project/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  expect_failures = [var.api_release]
}

run "reject_other_environment" {
  command = plan
  variables {
    api_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-production/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  expect_failures = [var.api_release]
}
