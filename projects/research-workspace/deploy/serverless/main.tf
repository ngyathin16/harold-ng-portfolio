locals {
  prefix = "research-${var.environment}"
  dispatcher_release = var.dispatcher_release == null ? {
    image = var.images.service, app_commit = var.app_commit
  } : var.dispatcher_release
  google_alert_budget = var.google_alert_budget == null ? {
    currency_code  = "USD"
    amount         = var.review.monthly_limit_usd
    usd_equivalent = var.review.monthly_limit_usd
  } : var.google_alert_budget
  apis = toset(["run.googleapis.com", "cloudtasks.googleapis.com", "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com", "artifactregistry.googleapis.com", "logging.googleapis.com",
  "monitoring.googleapis.com", "cloudbilling.googleapis.com", "billingbudgets.googleapis.com", "iam.googleapis.com", "storage.googleapis.com"])
  identities = toset(["api", "dispatcher", "search", "tasks", "scheduler", "backup", "verify", "prune", "cleanup", "import", "restore"])
  secrets = setunion(toset(["api-db", "dispatcher-db", "search-db", "backup-db", "verify-db", "prune-db", "cleanup-db", "import-db", "restore-db", "session",
    "r2-endpoint", "r2-bucket", "r2-access", "r2-secret", "objects-endpoint", "objects-bucket", "objects-access", "objects-secret"]),
  var.result_storage == null ? toset([]) : toset(["result-api", "result-worker", "result-operator"]))
  operator_commands = {
    backup = ["backup"], verify = ["verify"], prune = ["prune"], cleanup = ["cleanup"], import = ["import"], restore = ["restore"]
  }
  r2_env = {
    R2_ENDPOINT        = "r2-endpoint", R2_BUCKET = "r2-bucket", R2_ACCESS_KEY_ID = "r2-access", R2_SECRET_ACCESS_KEY = "r2-secret",
    OBJECT_R2_ENDPOINT = "objects-endpoint", OBJECT_R2_BUCKET = "objects-bucket", OBJECT_R2_ACCESS_KEY_ID = "objects-access", OBJECT_R2_SECRET_ACCESS_KEY = "objects-secret"
  }
  operator_object_secrets = var.result_storage == null ? local.r2_env : { RESEARCH_RESULT_CREDENTIALS_JSON = "result-operator" }
  result_env = var.result_storage == null ? {} : {
    RESEARCH_RESULT_STORAGE_MODE      = "objects"
    RESEARCH_RESULT_BUCKET            = var.result_storage.bucket
    RESEARCH_RESULT_PREFIX            = var.result_storage.prefix
    RESEARCH_RESULT_RESERVATION_BYTES = tostring(var.result_storage.reservation_bytes)
    RESEARCH_DATABASE_HEADROOM_BYTES  = tostring(var.result_storage.headroom_bytes)
  }
  secret_access = merge(
    { for role in ["api", "dispatcher", "search", "backup", "verify", "prune", "cleanup", "import", "restore"] : "${role}-db" => { role = role, secret = "${role}-db" }
    },
    { session = { role = "api", secret = "session" }, restore-source-lock = { role = "restore", secret = "backup-db" }
    },
    { for pair in setproduct(["backup", "verify", "prune", "cleanup", "import", "restore"], values(local.operator_object_secrets)) : "${pair[0]}-${pair[1]}" => { role = pair[0], secret = pair[1] }
    },
    var.result_storage == null ? {} : {
      api-results    = { role = "api", secret = "result-api" }
      search-results = { role = "search", secret = "result-worker" }
    }
  )
  budget_env = var.budget_window_id == "" ? {} : { RESEARCH_BUDGET_WINDOW_ID = var.budget_window_id }
  common_env = merge(local.budget_env, {
    SSL_CERT_FILE                      = "/usr/local/lib/python3.12/site-packages/certifi/cacert.pem",
    RESEARCH_DEPLOYMENT_MODE           = "serverless", RESEARCH_APP_COMMIT = var.app_commit,
    RESEARCH_MAX_SECONDS               = "120", RESEARCH_MAX_ATTEMPTS = "3", RESEARCH_WORKER_CONCURRENCY = "2", RESEARCH_MAX_QUEUED = "100",
    RESEARCH_CLIENT_IP_MODE            = "shared", RESEARCH_MAX_OUTSTANDING = "2", RESEARCH_MAX_LAUNCHES_PER_MINUTE = "4",
    RESEARCH_MAX_LAUNCH_ATTEMPTS       = "3", RESEARCH_DISPATCH_BATCH_SIZE = "2", RESEARCH_CAPACITY_MAX_AGE_SECONDS = "900",
    RESEARCH_RESULT_RESERVATION_BYTES  = var.result_storage == null ? "2147483648" : tostring(var.result_storage.reservation_bytes),
    RESEARCH_DATABASE_HEADROOM_BYTES   = var.result_storage == null ? "1073741824" : tostring(var.result_storage.headroom_bytes)
    RESEARCH_CAPACITY_MEASUREMENT_MODE = var.result_storage == null ? "manual" : var.result_storage.capacity_measurement_mode
    RESEARCH_CAPACITY_LIMIT_BYTES      = var.result_storage == null ? "1073741824" : tostring(var.result_storage.capacity_limit_bytes)
  })
  dispatch_marker_permissions = {
    api        = ["storage.objects.create"]
    dispatcher = ["storage.objects.list", "storage.objects.delete"]
    recovery   = ["storage.objects.create", "storage.objects.list"]
  }
  dispatch_marker_env = {
    RESEARCH_DISPATCH_MARKER_BUCKET = google_storage_bucket.dispatch_markers.name
    RESEARCH_DISPATCH_MARKER_PREFIX = "dispatch/"
  }
}

resource "google_project_service" "api" {
  for_each           = local.apis
  service            = each.key
  disable_on_destroy = false
  lifecycle {
    precondition {
      condition     = var.environment != "staging" || var.review.experiment_limit_usd > 0
      error_message = "A zero experiment allocation permits offline work only; staging provisioning needs an explicit positive allocation within the monthly ceiling."
    }
  }
}
data "google_project" "current" {
  project_id = var.project_id
}
resource "google_service_account" "identity" {
  for_each     = local.identities
  account_id   = "${local.prefix}-${each.key}"
  display_name = "Research ${var.environment} ${each.key}"
  depends_on   = [google_project_service.api]
}
resource "google_secret_manager_secret" "secret" {
  for_each  = local.secrets
  secret_id = "${local.prefix}-${each.key}"
  replication {
    auto {}
  }
  lifecycle { prevent_destroy = true }
  depends_on = [google_project_service.api]
}
resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = local.secret_access
  secret_id = google_secret_manager_secret.secret[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.identity[each.value.role].email}"
}
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = local.prefix
  format        = "DOCKER"
  vulnerability_scanning_config { enablement_config = "DISABLED" }
  depends_on = [google_project_service.api]
}
resource "google_artifact_registry_repository_iam_member" "dispatcher_images" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.identity["dispatcher"].email}"
}

resource "google_storage_bucket" "dispatch_markers" {
  name                        = "${var.project_id}-${local.prefix}-dispatch"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = false }
  soft_delete_policy { retention_duration_seconds = 0 }
  lifecycle { prevent_destroy = true }
  depends_on = [google_project_service.api]
}
resource "google_project_iam_custom_role" "dispatch_markers" {
  for_each    = local.dispatch_marker_permissions
  role_id     = "research_${var.environment}_marker_${each.key}"
  title       = "Research ${var.environment} dispatch markers ${each.key}"
  permissions = each.value
  depends_on  = [google_project_service.api]
}
resource "google_storage_bucket_iam_member" "dispatch_markers" {
  for_each = local.dispatch_marker_permissions
  bucket   = google_storage_bucket.dispatch_markers.name
  role     = google_project_iam_custom_role.dispatch_markers[each.key].name
  member   = each.key == "recovery" ? var.recovery_principal : "serviceAccount:${google_service_account.identity[each.key].email}"
  lifecycle {
    precondition {
      condition = each.key != "recovery" || !contains(concat(["allUsers", "allAuthenticatedUsers"], [
        for identity in local.identities : "serviceAccount:${local.prefix}-${identity}@${var.project_id}.iam.gserviceaccount.com"
      ]), var.recovery_principal)
      error_message = "Dispatch marker recovery requires an independently authenticated principal, never an application service account."
    }
  }
}

resource "google_cloud_tasks_queue" "dispatch" {
  name     = "${local.prefix}-dispatch"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 2
    max_dispatches_per_second = 1
  }
  retry_config {
    max_attempts       = 5
    max_retry_duration = "0s"
    min_backoff        = "5s"
    max_backoff        = "60s"
    max_doublings      = 3
  }
  depends_on = [google_project_service.api]
}
resource "google_cloud_tasks_queue_iam_member" "enqueue" {
  name     = google_cloud_tasks_queue.dispatch.name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.identity["api"].email}"
}
resource "google_service_account_iam_member" "task_identity" {
  service_account_id = google_service_account.identity["tasks"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.identity["api"].email}"
}

resource "google_cloud_run_v2_service" "dispatcher" {
  name                = "${local.prefix}-dispatcher"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = true
  scaling {
    min_instance_count = 0
    max_instance_count = 2
  }
  template {
    service_account                  = google_service_account.identity["dispatcher"].email
    timeout                          = "180s"
    max_instance_request_concurrency = 1
    containers {
      image   = local.dispatcher_release.image
      command = ["python", "-m", "uvicorn"]
      args    = ["service.dispatcher:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-proxy-headers"]
      resources {
        limits            = { cpu = "1", memory = "512Mi" }
        startup_cpu_boost = false
        cpu_idle          = true
      }
      dynamic "env" {
        for_each = merge(local.common_env, local.dispatch_marker_env, {
          RESEARCH_POOL_SIZE  = "2", RESEARCH_DISPATCH_JOB = google_cloud_run_v2_job.search.id,
          RESEARCH_APP_COMMIT = local.dispatcher_release.app_commit
        })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "RESEARCH_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secret["dispatcher-db"].secret_id
            version = var.secret_versions["dispatcher-db"]
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access, google_storage_bucket_iam_member.dispatch_markers["dispatcher"],
  google_artifact_registry_repository_iam_member.dispatcher_images]
}
resource "google_cloud_run_v2_service_iam_member" "dispatch" {
  for_each = local.reconciliation_enabled ? toset(["tasks"]) : toset(["tasks", "scheduler"])
  name     = google_cloud_run_v2_service.dispatcher.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.identity[each.key].email}"
  dynamic "condition" {
    for_each = local.reconciliation_enabled ? [local.reconciliation_deadline_condition] : []
    content {
      title      = condition.value.title
      expression = condition.value.expression
    }
  }
}

resource "google_cloud_run_v2_service" "api" {
  name                = "${local.prefix}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true
  scaling {
    min_instance_count = 0
    max_instance_count = 2
  }
  template {
    service_account                  = google_service_account.identity["api"].email
    timeout                          = "15s"
    max_instance_request_concurrency = 16
    containers {
      image   = var.images.service
      command = ["python", "-m", "uvicorn"]
      args    = ["service.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-proxy-headers"]
      resources {
        limits            = { cpu = "1", memory = "512Mi" }
        startup_cpu_boost = false
        cpu_idle          = true
      }
      startup_probe {
        http_get { path = "/live" }
      }
      liveness_probe {
        http_get { path = "/live" }
      }
      dynamic "env" {
        for_each = merge(local.common_env, local.dispatch_marker_env, local.result_env, {
          RESEARCH_POOL_SIZE            = var.budget_window_id == "" ? "4" : "3", RESEARCH_CLIENT_IP_MODE = "shared", RESEARCH_ALLOWED_ORIGINS = join(",", var.allowed_origins),
          RESEARCH_TASK_QUEUE           = google_cloud_tasks_queue.dispatch.id, RESEARCH_DISPATCHER_URL = google_cloud_run_v2_service.dispatcher.uri,
          RESEARCH_TASK_SERVICE_ACCOUNT = google_service_account.identity["tasks"].email
        })
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = merge({ RESEARCH_DATABASE_URL = "api-db", RESEARCH_SESSION_SECRET = "session" },
        var.result_storage == null ? {} : { RESEARCH_RESULT_CREDENTIALS_JSON = "result-api" })
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret[env.value].secret_id
              version = var.secret_versions[env.value]
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access, google_cloud_tasks_queue_iam_member.enqueue, google_storage_bucket_iam_member.dispatch_markers["api"]]
}
resource "google_cloud_run_v2_service_iam_member" "api" {
  for_each = var.public_api ? setunion(var.rehearsal_invokers, toset(["allUsers"])) : var.rehearsal_invokers
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.key
}

resource "google_cloud_run_v2_job" "search" {
  name                = "${local.prefix}-search"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.identity["search"].email
      max_retries     = 0
      timeout         = "300s"
      containers {
        image   = var.images.service
        command = ["python", "-m", "service.worker"]
        args    = ["--bound"]
        resources {
          limits = { cpu = "2", memory = "2Gi" }
        }
        dynamic "env" {
          for_each = merge(local.common_env, local.result_env, { RESEARCH_POOL_SIZE = "2" })
          content {
            name  = env.key
            value = env.value
          }
        }
        env {
          name = "RESEARCH_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret["search-db"].secret_id
              version = var.secret_versions["search-db"]
            }
          }
        }
        dynamic "env" {
          for_each = var.result_storage == null ? {} : { RESEARCH_RESULT_CREDENTIALS_JSON = "result-worker" }
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secret[env.value].secret_id
                version = var.secret_versions[env.value]
              }
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access]
}
resource "google_project_iam_custom_role" "launch" {
  role_id     = "research_${var.environment}_launch"
  title       = "Research bound search launch"
  permissions = ["run.jobs.get", "run.jobs.update", "run.jobs.run", "run.executions.get", "run.executions.list"]
}
resource "google_cloud_run_v2_job_iam_member" "launch" {
  name     = google_cloud_run_v2_job.search.name
  location = var.region
  role     = google_project_iam_custom_role.launch.name
  member   = "serviceAccount:${google_service_account.identity["dispatcher"].email}"
}
resource "google_service_account_iam_member" "search_identity" {
  service_account_id = google_service_account.identity["search"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.identity["dispatcher"].email}"
}
resource "google_project_iam_custom_role" "operations" {
  role_id     = "research_${var.environment}_operations"
  title       = "Research launch operation observation"
  permissions = ["run.operations.get"]
}
resource "google_project_iam_member" "operations" {
  project = var.project_id
  role    = google_project_iam_custom_role.operations.name
  member  = "serviceAccount:${google_service_account.identity["dispatcher"].email}"
}

resource "google_cloud_run_v2_job" "operator" {
  for_each            = local.operator_commands
  name                = "${local.prefix}-${each.key}"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.identity[each.key].email
      max_retries     = 0
      timeout         = "1800s"
      containers {
        image   = var.images.operator
        command = ["python", "-m", "ops.serverless"]
        args    = each.value
        resources {
          limits = { cpu = "1", memory = "4Gi" }
        }
        dynamic "env" {
          for_each = merge(local.result_env, local.budget_env, {
            SSL_CERT_FILE                    = local.common_env.SSL_CERT_FILE,
            RESEARCH_DEPLOYMENT_MODE         = "serverless", RESEARCH_CLIENT_IP_MODE = "shared",
            RESEARCH_OPERATOR_ENDPOINT_MODE  = "direct",
            RESEARCH_APP_COMMIT              = var.app_commit, RESEARCH_OPERATOR_MAX_STAGING_BYTES = tostring(var.operator_staging_bytes),
            RESEARCH_OPERATOR_MAX_DUMP_BYTES = tostring(var.operator_dump_bytes), RESEARCH_OPERATOR_TIMEOUT_SECONDS = "1500",
            RESEARCH_BACKUP_PREFIX           = var.r2_namespaces.backups, RESEARCH_OBJECT_PREFIX = var.r2_namespaces.objects, RESEARCH_REPORT_PREFIX = var.r2_namespaces.reports
          })
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = merge({ RESEARCH_OPERATOR_DATABASE_URL = "${each.key}-db" }, local.operator_object_secrets,
          each.key == "restore" ? { RESEARCH_BACKUP_DATABASE_URL = "backup-db" } : {})
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secret[env.value].secret_id
                version = var.secret_versions[env.value]
              }
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access]
}
resource "google_cloud_run_v2_job_iam_member" "operator_schedule" {
  for_each = toset(["backup", "prune", "cleanup"])
  name     = google_cloud_run_v2_job.operator[each.key].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.identity["scheduler"].email}"
  dynamic "condition" {
    for_each = local.reconciliation_enabled ? [local.reconciliation_window_condition] : []
    content {
      title      = condition.value.title
      expression = condition.value.expression
    }
  }
}
resource "google_cloud_run_v2_job_iam_member" "recovery" {
  for_each = local.operator_commands
  name     = google_cloud_run_v2_job.operator[each.key].name
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = var.recovery_principal
}

resource "google_cloud_scheduler_job" "reconcile" {
  name             = "${local.prefix}-reconcile"
  region           = var.region
  schedule         = "*/5 * * * *"
  time_zone        = "Asia/Hong_Kong"
  paused           = !var.enable_reconciliation
  attempt_deadline = "180s"
  retry_config {
    retry_count          = 0
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "3600s"
    max_doublings        = 5
  }
  http_target {
    http_method = "POST"
    uri         = local.reconciliation_enabled ? local.reconciliation_execution_uri : "${google_cloud_run_v2_service.dispatcher.uri}/dispatch"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    dynamic "oidc_token" {
      for_each = local.reconciliation_enabled ? [] : [true]
      content {
        service_account_email = google_service_account.identity["scheduler"].email
        audience              = google_cloud_run_v2_service.dispatcher.uri
      }
    }
    dynamic "oauth_token" {
      for_each = local.reconciliation_enabled ? [true] : []
      content {
        service_account_email = google_service_account.identity["scheduler"].email
      }
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.dispatch, google_workflows_workflow.reconciliation,
  google_project_iam_member.reconciliation_execute, google_monitoring_alert_policy.reconciliation]
}
resource "google_cloud_scheduler_job" "operator" {
  for_each         = { backup = "0 3 * * *", prune = "0 5 * * *", cleanup = "0 6 * * *" }
  name             = "${local.prefix}-${each.key}"
  region           = var.region
  schedule         = each.value
  time_zone        = "Asia/Hong_Kong"
  paused           = !var.enable_operator_schedules
  attempt_deadline = "60s"
  retry_config {
    retry_count          = 0
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "3600s"
    max_doublings        = 5
  }
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.operator[each.key].id}:run"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    oauth_token { service_account_email = google_service_account.identity["scheduler"].email }
  }
  lifecycle {
    precondition {
      condition     = !var.enable_operator_schedules || (length(var.recovery_and_alert_evidence) > 3 && length(var.notification_channels) > 0)
      error_message = "Unattended operators require the managed R2 drill, cost review and delivered notification evidence."
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.operator_schedule]
}

resource "google_monitoring_alert_policy" "operator_failure" {
  display_name          = "${local.prefix} failed execution"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "Failed bounded Job"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_job\" AND metric.type = \"run.googleapis.com/job/completed_execution_count\" AND metric.label.result = \"failed\" AND resource.label.job_name = starts_with(\"${local.prefix}-\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_DELTA"
      }
    }
  }
  documentation {
    content   = "Inspect sanitized execution reports. Pause new search admission for capacity/dispatch failures; preserve accepted queries and the last verified backup. See docs/RESEARCH-SERVERLESS.md."
    mime_type = "text/markdown"
  }
  depends_on = [google_project_service.api]
}
resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "${local.prefix}-monthly-alert-not-cap"
  budget_filter { projects = ["projects/${data.google_project.current.number}"] }
  amount {
    specified_amount {
      currency_code = local.google_alert_budget.currency_code
      units         = tostring(floor(local.google_alert_budget.amount))
      nanos         = (local.google_alert_budget.amount * 100 - floor(local.google_alert_budget.amount) * 100) * 10000000
    }
  }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }
  all_updates_rule { monitoring_notification_channels = var.notification_channels }
  depends_on = [google_project_service.api]
}

resource "google_monitoring_alert_policy" "dispatch" {
  for_each = {
    queue_age        = "jsonPayload.queue_age_seconds > 60",
    uncertain_launch = "jsonPayload.uncertain > 0",
    expired_lease    = "jsonPayload.expired_leases > 0"
  }
  display_name          = "${local.prefix}-${each.key}"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = each.key
    condition_matched_log {
      filter = "resource.type=\"cloud_run_revision\" resource.labels.service_name=\"${local.prefix}-dispatcher\" jsonPayload.event=\"research_dispatch\" ${each.value}"
    }
  }
  alert_strategy {
    notification_rate_limit { period = "300s" }
    auto_close = "3600s"
  }
  documentation {
    content   = "Check accepted queue state and Cloud Run execution identity. Do not release an uncertain reservation solely because its lease expired. See docs/RESEARCH-SERVERLESS.md."
    mime_type = "text/markdown"
  }
  depends_on = [google_project_service.api]
}

output "api_uri" { value = google_cloud_run_v2_service.api.uri }
output "dispatcher_uri" { value = google_cloud_run_v2_service.dispatcher.uri }
output "dispatch_marker_bucket" { value = google_storage_bucket.dispatch_markers.name }
output "secret_names" {
  value = { for name, secret in google_secret_manager_secret.secret : name => secret.secret_id }
}
