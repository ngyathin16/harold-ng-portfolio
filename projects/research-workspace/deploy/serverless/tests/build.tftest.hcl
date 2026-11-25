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

run "github_build_is_disabled_by_default" {
  command = plan
  assert {
    condition = (
      length(google_project_service.build) == 0 &&
      length(google_iam_workload_identity_pool.github_build) == 0 &&
      length(google_iam_workload_identity_pool_provider.github_build) == 0 &&
      length(google_service_account.github_build) == 0 &&
      length(google_service_account_iam_member.github_build) == 0 &&
      length(google_artifact_registry_repository_iam_member.github_build) == 0
    )
    error_message = "The default deployment must create no GitHub build federation, account, API enablements, or grants."
  }
}

run "github_build_only_writes_environment_images" {
  command = plan
  variables { enable_github_image_build = true }
  assert {
    condition     = google_artifact_registry_repository.images.vulnerability_scanning_config[0].enablement_config == "DISABLED"
    error_message = "Image publication must not enable unpriced vulnerability scanning."
  }

  override_data {
    target          = data.google_project.current
    override_during = plan
    values          = { number = "123456789012" }
  }
  override_resource {
    target          = google_iam_workload_identity_pool.github_build[0]
    override_during = plan
    values          = { name = "projects/123456789012/locations/global/workloadIdentityPools/research-staging-build" }
  }
  override_resource {
    target          = google_service_account.github_build[0]
    override_during = plan
    values = {
      name  = "projects/research-offline-fixture/serviceAccounts/research-staging-build@research-offline-fixture.iam.gserviceaccount.com"
      email = "research-staging-build@research-offline-fixture.iam.gserviceaccount.com"
    }
  }
  override_resource {
    target          = google_service_account.identity["dispatcher"]
    override_during = plan
    values          = { email = "research-staging-dispatcher@research-offline-fixture.iam.gserviceaccount.com" }
  }

  assert {
    condition = (
      length(google_iam_workload_identity_pool.github_build) == 1 &&
      length(google_iam_workload_identity_pool_provider.github_build) == 1 &&
      length(google_service_account.github_build) == 1 &&
      length(google_service_account_iam_member.github_build) == 1 &&
      length(google_artifact_registry_repository_iam_member.github_build) == 1
    )
    error_message = "Opting in must create exactly one isolated federation provider, build identity, and each required grant."
  }
  assert {
    condition = (
      google_iam_workload_identity_pool.github_build[0].project == "123456789012" &&
      google_iam_workload_identity_pool_provider.github_build[0].project == "123456789012" &&
      google_iam_workload_identity_pool.github_build[0].project != var.project_id
    )
    error_message = "Federation must use the canonical project number returned by Google, preserving imported pools and providers without destructive replacement."
  }
  assert {
    condition = (
      google_iam_workload_identity_pool_provider.github_build[0].oidc[0].issuer_uri == "https://token.actions.githubusercontent.com" &&
      google_iam_workload_identity_pool_provider.github_build[0].workload_identity_pool_id == google_iam_workload_identity_pool.github_build[0].workload_identity_pool_id &&
      google_iam_workload_identity_pool.github_build[0].workload_identity_pool_id == "research-staging-build" &&
      google_iam_workload_identity_pool_provider.github_build[0].attribute_mapping["google.subject"] == "assertion.sub" &&
      google_iam_workload_identity_pool_provider.github_build[0].attribute_mapping["attribute.repository_id"] == "assertion.repository_id"
    )
    error_message = "Only GitHub's OIDC issuer may supply the subject and immutable repository identity."
  }
  assert {
    condition = toset(split(" && ", google_iam_workload_identity_pool_provider.github_build[0].attribute_condition)) == toset([
      "assertion.repository_id == '1459453007'",
      "assertion.repository_owner_id == '115745691'",
      "assertion.repository == 'ngyathin16/harold-ng-portfolio'",
      "assertion.ref == 'refs/heads/feature/research-workspace'",
      "assertion.workflow_ref == 'ngyathin16/harold-ng-portfolio/.github/workflows/research-image-build.yml@refs/heads/feature/research-workspace'",
      "(assertion.event_name == 'push' || assertion.event_name == 'workflow_dispatch')"
    ])
    error_message = "Federation must require the exact repository and owner IDs, repository name, feature branch, build workflow, and explicit push or manual event together."
  }
  assert {
    condition = (
      toset(keys(google_project_service.build)) == toset(["sts.googleapis.com", "iamcredentials.googleapis.com"]) &&
      alltrue([for name, api in google_project_service.build : api.service == name && !api.disable_on_destroy]) &&
      google_service_account.github_build[0].account_id == "research-staging-build" &&
      google_service_account_iam_member.github_build[0].member == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/research-staging-build/attribute.repository_id/1459453007"
    )
    error_message = "Build bootstrap may enable only token exchange and identity credentials, and impersonation must select the immutable trusted repository ID."
  }
  assert {
    condition = (
      google_service_account_iam_member.github_build[0].service_account_id == google_service_account.github_build[0].name &&
      google_service_account_iam_member.github_build[0].role == "roles/iam.workloadIdentityUser" &&
      google_artifact_registry_repository_iam_member.github_build[0].project == var.project_id &&
      google_artifact_registry_repository_iam_member.github_build[0].location == var.region &&
      google_artifact_registry_repository_iam_member.github_build[0].repository == google_artifact_registry_repository.images.repository_id &&
      google_artifact_registry_repository_iam_member.github_build[0].role == "roles/artifactregistry.writer" &&
      google_artifact_registry_repository_iam_member.github_build[0].member == "serviceAccount:${google_service_account.github_build[0].email}"
    )
    error_message = "The federated identity may impersonate only its build account, which may write only this environment's image repository."
  }
  assert {
    condition = (
      google_project_iam_member.operations.member != "serviceAccount:${google_service_account.github_build[0].email}" &&
      alltrue([for account in google_service_account.identity : account.account_id != google_service_account.github_build[0].account_id]) &&
      alltrue([for grant in values(local.secret_access) : !contains(["build", "github-build"], grant.role)])
    )
    error_message = "The build identity must not inherit runtime identities, application secrets, or the project-wide operation-observation grant."
  }
}
