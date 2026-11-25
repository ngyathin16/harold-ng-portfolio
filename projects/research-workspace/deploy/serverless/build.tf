variable "enable_github_image_build" {
  type        = bool
  default     = false
  description = "Enable the explicitly requested, repository-scoped GitHub image publisher after the rehearsal budget review."
}

locals {
  github_build_repository    = "ngyathin16/harold-ng-portfolio"
  github_build_repository_id = "1459453007"
  github_build_owner_id      = "115745691"
  github_build_ref           = "refs/heads/feature/research-workspace"
  github_build_workflow_ref  = "${local.github_build_repository}/.github/workflows/research-image-build.yml@${local.github_build_ref}"
  github_build_condition = join(" && ", [
    "assertion.repository_id == '${local.github_build_repository_id}'",
    "assertion.repository_owner_id == '${local.github_build_owner_id}'",
    "assertion.repository == '${local.github_build_repository}'",
    "assertion.ref == '${local.github_build_ref}'",
    "assertion.workflow_ref == '${local.github_build_workflow_ref}'",
    "(assertion.event_name == 'push' || assertion.event_name == 'workflow_dispatch')",
  ])
}

resource "google_project_service" "build" {
  for_each           = var.enable_github_image_build ? toset(["sts.googleapis.com", "iamcredentials.googleapis.com"]) : toset([])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "github_build" {
  count                     = var.enable_github_image_build ? 1 : 0
  project                   = data.google_project.current.number
  workload_identity_pool_id = "${local.prefix}-build"
  display_name              = "Research ${var.environment} builds"
  depends_on                = [google_project_service.api["iam.googleapis.com"], google_project_service.build]
}

resource "google_iam_workload_identity_pool_provider" "github_build" {
  count                              = var.enable_github_image_build ? 1 : 0
  project                            = data.google_project.current.number
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_build[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }
  attribute_condition = local.github_build_condition
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_build" {
  count        = var.enable_github_image_build ? 1 : 0
  project      = var.project_id
  account_id   = "${local.prefix}-build"
  display_name = "Research ${var.environment} image publisher"
  depends_on   = [google_project_service.api["iam.googleapis.com"], google_project_service.build]
}

resource "google_service_account_iam_member" "github_build" {
  count              = var.enable_github_image_build ? 1 : 0
  service_account_id = google_service_account.github_build[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_build[0].name}/attribute.repository_id/${local.github_build_repository_id}"
}

resource "google_artifact_registry_repository_iam_member" "github_build" {
  count      = var.enable_github_image_build ? 1 : 0
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_build[0].email}"
}

output "github_build_identity" {
  value = var.enable_github_image_build ? {
    workload_identity_provider = google_iam_workload_identity_pool_provider.github_build[0].name
    service_account            = google_service_account.github_build[0].email
    repository                 = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
  } : null
}
