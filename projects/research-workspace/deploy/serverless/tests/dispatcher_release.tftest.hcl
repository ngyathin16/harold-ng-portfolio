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

run "dispatcher_reads_only_the_environment_image_repository" {
  command = plan
  assert {
    condition = (
      google_artifact_registry_repository_iam_member.dispatcher_images.project == "research-offline-fixture" &&
      google_artifact_registry_repository_iam_member.dispatcher_images.location == "asia-southeast1" &&
      google_artifact_registry_repository_iam_member.dispatcher_images.repository == "research-staging" &&
      google_artifact_registry_repository_iam_member.dispatcher_images.role == "roles/artifactregistry.reader" &&
      google_artifact_registry_repository_iam_member.dispatcher_images.member == "serviceAccount:research-staging-dispatcher@research-offline-fixture.iam.gserviceaccount.com"
    )
    error_message = "The Job updater may read images only from this environment's repository, using its own identity and no writer or project-wide grant."
  }
}

run "null_override_preserves_base_release" {
  command = plan
  assert {
    condition = (
      google_cloud_run_v2_service.dispatcher.template[0].containers[0].image == var.images.service &&
      one([for env in google_cloud_run_v2_service.dispatcher.template[0].containers[0].env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.app_commit
    )
    error_message = "The default dispatcher must retain the base service image and exact source commit."
  }
}

run "override_changes_only_dispatcher_release" {
  command = plan
  variables {
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  assert {
    condition = (
      google_cloud_run_v2_service.dispatcher.template[0].containers[0].image == var.dispatcher_release.image &&
      one([for env in google_cloud_run_v2_service.dispatcher.template[0].containers[0].env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.dispatcher_release.app_commit &&
      google_cloud_run_v2_service.dispatcher.template[0].timeout == "180s" &&
      google_cloud_run_v2_service.dispatcher.template[0].max_instance_request_concurrency == 1 &&
      google_cloud_run_v2_service.dispatcher.ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY" &&
      google_cloud_run_v2_service.dispatcher.template[0].containers[0].resources[0].limits == tomap({ cpu = "1", memory = "512Mi" })
    )
    error_message = "Only the dispatcher's image and truthful source commit may change; its execution bounds and ingress stay fixed."
  }
  assert {
    condition = (
      google_cloud_run_v2_service.api.template[0].containers[0].image == var.images.service &&
      google_cloud_run_v2_job.search.template[0].template[0].containers[0].image == var.images.service &&
      alltrue([for job in values(google_cloud_run_v2_job.operator) : job.template[0].template[0].containers[0].image == var.images.operator]) &&
      alltrue([for container in concat(
        [google_cloud_run_v2_service.api.template[0].containers[0], google_cloud_run_v2_job.search.template[0].template[0].containers[0]],
        [for job in values(google_cloud_run_v2_job.operator) : job.template[0].template[0].containers[0]]
      ) : one([for env in container.env : env.value if env.name == "RESEARCH_APP_COMMIT"]) == var.app_commit])
    )
    error_message = "A dispatcher correction must preserve the API, reserved search Job, and all six operator image/commit pairs."
  }
  assert {
    condition = (
      google_cloud_run_v2_job.search.template[0].task_count == 1 &&
      google_cloud_run_v2_job.search.template[0].parallelism == 1 &&
      google_cloud_run_v2_job.search.template[0].template[0].max_retries == 0 &&
      google_cloud_run_v2_job.search.template[0].template[0].timeout == "300s" &&
      google_cloud_run_v2_job.search.template[0].template[0].containers[0].args == tolist(["--bound"]) &&
      length(google_cloud_run_v2_service_iam_member.api) == 0 &&
      google_cloud_scheduler_job.reconcile.paused
    )
    error_message = "Dispatcher release selection must not change search bounds, publish the API, or enable a schedule."
  }
}

run "reject_partial_commit" {
  command = plan
  variables {
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = null
    }
  }
  expect_failures = [var.dispatcher_release]
}

run "reject_partial_image" {
  command = plan
  variables {
    dispatcher_release = { image = null, app_commit = "dddddddddddddddddddddddddddddddddddddddd" }
  }
  expect_failures = [var.dispatcher_release]
}

run "reject_short_commit" {
  command = plan
  variables {
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "ddddddd"
    }
  }
  expect_failures = [var.dispatcher_release]
}

run "reject_mutable_image" {
  command = plan
  variables {
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-staging/service:latest"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  expect_failures = [var.dispatcher_release]
}

run "reject_other_repository" {
  command = plan
  variables {
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/another-project/research-staging/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  expect_failures = [var.dispatcher_release]
}

run "reject_other_environment" {
  command = plan
  variables {
    dispatcher_release = {
      image      = "asia-southeast1-docker.pkg.dev/research-offline-fixture/research-production/service@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      app_commit = "dddddddddddddddddddddddddddddddddddddddd"
    }
  }
  expect_failures = [var.dispatcher_release]
}
