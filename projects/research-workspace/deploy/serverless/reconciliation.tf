locals {
  reconciliation_enabled = var.finite_reconciliation != null
  reconciliation_counter = "reconciliation-budget/${var.budget_window_id}"
  reconciliation_deadline_condition = var.finite_reconciliation == null ? null : {
    title      = "reviewed_reconciliation_deadline"
    expression = "request.time < timestamp(\"${var.finite_reconciliation.expires_at}\")"
  }
  reconciliation_window_condition = var.finite_reconciliation == null ? null : {
    title      = "reviewed_reconciliation_window"
    expression = "request.time >= timestamp(\"${var.finite_reconciliation.starts_at}\") && request.time < timestamp(\"${var.finite_reconciliation.expires_at}\")"
  }
  reconciliation_execution_uri = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${local.prefix}-reconcile-gate/executions"
}

resource "google_project_service" "reconciliation" {
  for_each           = local.reconciliation_enabled ? toset(["workflows.googleapis.com", "workflowexecutions.googleapis.com"]) : toset([])
  service            = each.key
  disable_on_destroy = false
}

resource "google_service_account" "reconciliation" {
  for_each     = local.reconciliation_enabled ? toset(["gate", "stopper"]) : toset([])
  account_id   = "${local.prefix}-${each.key}"
  display_name = "Research ${var.environment} finite reconciliation ${each.key}"
  depends_on   = [google_project_service.api]
}

resource "google_storage_bucket_object" "reconciliation_budget" {
  count        = local.reconciliation_enabled ? 1 : 0
  bucket       = google_storage_bucket.dispatch_markers.name
  name         = local.reconciliation_counter
  content      = "{}"
  content_type = "application/json"
  metadata = {
    window    = var.budget_window_id
    deadline  = var.finite_reconciliation.expires_at
    remaining = tostring(var.finite_reconciliation.dispatch_limit)
  }
  lifecycle {
    ignore_changes  = [metadata]
    prevent_destroy = true
  }
}

resource "google_project_iam_custom_role" "reconciliation" {
  for_each = local.reconciliation_enabled ? {
    list    = ["storage.objects.list"]
    counter = ["storage.objects.get", "storage.objects.update"]
    execute = ["workflows.executions.create"]
    pause   = ["cloudscheduler.jobs.pause"]
    logs    = ["logging.logEntries.create"]
  } : {}
  role_id     = "research_${var.environment}_reconciliation_${each.key}"
  title       = "Research finite reconciliation ${each.key}"
  permissions = each.value
  depends_on  = [google_project_service.api, google_project_service.reconciliation]
}

resource "google_storage_bucket_iam_member" "reconciliation_list" {
  count  = local.reconciliation_enabled ? 1 : 0
  bucket = google_storage_bucket.dispatch_markers.name
  role   = google_project_iam_custom_role.reconciliation["list"].name
  member = "serviceAccount:${google_service_account.reconciliation["gate"].email}"
  condition {
    title      = local.reconciliation_window_condition.title
    expression = local.reconciliation_window_condition.expression
  }
}

resource "google_storage_bucket_iam_member" "reconciliation_counter" {
  count  = local.reconciliation_enabled ? 1 : 0
  bucket = google_storage_bucket.dispatch_markers.name
  role   = google_project_iam_custom_role.reconciliation["counter"].name
  member = "serviceAccount:${google_service_account.reconciliation["gate"].email}"
  condition {
    title      = "reviewed_reconciliation_counter"
    expression = "resource.name == \"projects/_/buckets/${google_storage_bucket.dispatch_markers.name}/objects/${local.reconciliation_counter}\" && ${local.reconciliation_window_condition.expression}"
  }
}

resource "google_cloud_run_v2_service_iam_member" "reconciliation_dispatch" {
  count    = local.reconciliation_enabled ? 1 : 0
  name     = google_cloud_run_v2_service.dispatcher.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.reconciliation["gate"].email}"
  condition {
    title      = local.reconciliation_window_condition.title
    expression = local.reconciliation_window_condition.expression
  }
}

resource "google_project_iam_member" "reconciliation_execute" {
  count   = local.reconciliation_enabled ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.reconciliation["execute"].name
  member  = "serviceAccount:${google_service_account.identity["scheduler"].email}"
  condition {
    title      = local.reconciliation_window_condition.title
    expression = local.reconciliation_window_condition.expression
  }
}

resource "google_project_iam_member" "reconciliation_pause" {
  count   = local.reconciliation_enabled ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.reconciliation["pause"].name
  member  = "serviceAccount:${google_service_account.reconciliation["stopper"].email}"
  condition {
    title      = "reviewed_reconciliation_pause_window"
    expression = "request.time >= timestamp(\"${var.finite_reconciliation.expires_at}\") && request.time < timestamp(\"${timeadd(var.finite_reconciliation.expires_at, "1h")}\")"
  }
}

resource "google_project_iam_member" "reconciliation_logs" {
  count   = local.reconciliation_enabled ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.reconciliation["logs"].name
  member  = "serviceAccount:${google_service_account.reconciliation["gate"].email}"
  condition {
    title      = "reviewed_reconciliation_error_logs"
    expression = "request.time < timestamp(\"${timeadd(var.finite_reconciliation.expires_at, "1h")}\")"
  }
}

resource "google_service_account_iam_member" "reconciliation_pause_creator" {
  count              = local.reconciliation_enabled ? 1 : 0
  service_account_id = google_service_account.reconciliation["stopper"].name
  role               = "roles/iam.serviceAccountUser"
  member             = var.recovery_principal
  lifecycle {
    precondition {
      condition = !contains([
        for suffix in ["gate", "stopper"] : "serviceAccount:${local.prefix}-${suffix}@${var.project_id}.iam.gserviceaccount.com"
      ], var.recovery_principal)
      error_message = "The finite gate and timer identities cannot be the independent recovery principal."
    }
  }
}

resource "google_workflows_workflow" "reconciliation" {
  count               = local.reconciliation_enabled ? 1 : 0
  name                = "${local.prefix}-reconcile-gate"
  region              = var.region
  service_account     = google_service_account.reconciliation["gate"].email
  deletion_protection = true
  call_log_level      = "LOG_ERRORS_ONLY"
  description         = "Finite reviewed marker gate; empty checks open no database or Cloud Run connection."
  source_contents = templatefile("${path.module}/reconciliation.yaml.tftpl", {
    marker_bucket  = google_storage_bucket.dispatch_markers.name
    counter_url    = "https://storage.googleapis.com/storage/v1/b/${google_storage_bucket.dispatch_markers.name}/o/${urlencode(local.reconciliation_counter)}"
    dispatcher_uri = google_cloud_run_v2_service.dispatcher.uri
    starts_at      = var.finite_reconciliation.starts_at
    expires_at     = var.finite_reconciliation.expires_at
    window         = var.budget_window_id
    dispatch_limit = var.finite_reconciliation.dispatch_limit
  })
  depends_on = [
    google_project_service.reconciliation, google_storage_bucket_object.reconciliation_budget,
    google_storage_bucket_iam_member.reconciliation_list, google_storage_bucket_iam_member.reconciliation_counter,
    google_cloud_run_v2_service_iam_member.reconciliation_dispatch, google_project_iam_member.reconciliation_logs
  ]
}

resource "google_monitoring_alert_policy" "reconciliation" {
  count                 = local.reconciliation_enabled ? 1 : 0
  display_name          = "${local.prefix} finite reconciliation failed"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "Workflow failed or cancelled"
    condition_threshold {
      filter          = "resource.type = \"workflows.googleapis.com/Workflow\" AND resource.label.workflow_id = \"${local.prefix}-reconcile-gate\" AND metric.type = \"workflows.googleapis.com/finished_execution_count\" AND metric.label.status != \"SUCCEEDED\" AND metric.label.status != \"succeeded\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_DELTA"
      }
    }
  }
  conditions {
    display_name = "Task delivery failed, including deadline timers"
    condition_threshold {
      filter          = "resource.type = \"cloud_tasks_queue\" AND resource.label.queue_id = \"${local.prefix}-dispatch\" AND metric.type = \"cloudtasks.googleapis.com/queue/task_attempt_count\" AND metric.label.response_code != \"ok\" AND metric.label.response_code != \"OK\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_DELTA"
      }
    }
  }
  conditions {
    display_name = "Task attempt more than two minutes late"
    condition_threshold {
      filter          = "resource.type = \"cloud_tasks_queue\" AND resource.label.queue_id = \"${local.prefix}-dispatch\" AND metric.type = \"cloudtasks.googleapis.com/queue/task_attempt_delays\""
      comparison      = "COMPARISON_GT"
      threshold_value = 120000
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }
  documentation {
    content   = "Inspect gate execution, counter and four Scheduler states. Preserve unknown debits and accepted work. A duplicate pause can fail after the job was already paused. Late-attempt metrics arrive only after dispatch; IAM expiry is the independent admission fence. Never refill the counter or extend the deadline automatically. Follow deploy/serverless/RECONCILIATION.md."
    mime_type = "text/markdown"
  }
  depends_on = [google_workflows_workflow.reconciliation]
}

output "reconciliation_pause_tasks" {
  description = "Create these four named one-time tasks through the independent operator, read back each complete payload and queue retry settings, then record evidence before enabling schedules. Terraform does not dispatch them."
  value = var.finite_reconciliation == null ? [] : [for job in ["reconcile", "backup", "prune", "cleanup"] : {
    parent = google_cloud_tasks_queue.dispatch.id
    task = {
      name             = "${google_cloud_tasks_queue.dispatch.id}/tasks/rehearsal-${var.budget_window_id}-${job}"
      scheduleTime     = var.finite_reconciliation.expires_at
      dispatchDeadline = "30s"
      retryConfig      = { maxAttempts = 5, maxRetryDuration = "600s", minBackoff = "150s", maxBackoff = "150s", maxDoublings = 0 }
      httpRequest = {
        url        = "https://cloudscheduler.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/jobs/${local.prefix}-${job}:pause"
        httpMethod = "POST"
        oauthToken = { serviceAccountEmail = google_service_account.reconciliation["stopper"].email, scope = "https://www.googleapis.com/auth/cloud-scheduler" }
      }
    }
  }]
}
