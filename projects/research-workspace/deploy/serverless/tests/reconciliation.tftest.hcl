mock_provider "google" {}

override_resource {
  target          = google_cloud_run_v2_service.dispatcher
  override_during = plan
  values          = { uri = "https://dispatcher-fixture.run.app" }
}
override_resource {
  target          = google_cloud_tasks_queue.dispatch
  override_during = plan
  values          = { id = "projects/research-offline-fixture/locations/asia-southeast1/queues/research-staging-dispatch" }
}

variables {
  project_id            = "research-offline-fixture"
  billing_account       = "000000-000000-000000"
  environment           = "staging"
  app_commit            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  recovery_principal    = "user:operator@example.invalid"
  budget_window_id      = "offline-reviewed-fixture"
  notification_channels = ["projects/research-offline-fixture/notificationChannels/fixture"]
  images = {
    service  = "example.invalid/service@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    operator = "example.invalid/operator@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
  review = {
    experiment_limit_usd = 3, monthly_limit_usd = 4.99, provider_plan = "OFFLINE TEST ONLY", database_region = "aws-ap-southeast-1",
    quote_date           = "2026-11-25", evidence_reference = "fixture: not deployment approval", approved = true
  }
  secret_versions = {
    api-db      = "1", dispatcher-db = "1", search-db = "1", backup-db = "1", verify-db = "1", prune-db = "1", cleanup-db = "1", import-db = "1", restore-db = "1", session = "1",
    r2-endpoint = "1", r2-bucket = "1", r2-access = "1", r2-secret = "1", objects-endpoint = "1", objects-bucket = "1", objects-access = "1", objects-secret = "1"
  }
  r2_namespaces = { objects = "staging/objects/", backups = "staging/backups/", reports = "staging/reports/" }
  finite_reconciliation = {
    starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T00:00:00Z",
    dispatch_limit            = 8, accepted_queries = 2,
    other_service_cpu_seconds = 1000, other_service_gib_seconds = 500, other_service_requests = 330,
    evidence_reference        = "fixture: no deployment qualification"
  }
}

run "disabled_gate_creates_no_resources" {
  command = plan
  variables { finite_reconciliation = null }
  assert {
    condition = (
      length(google_workflows_workflow.reconciliation) == 0 && length(google_storage_bucket_object.reconciliation_budget) == 0 &&
      length(google_service_account.reconciliation) == 0 && length(google_project_service.reconciliation) == 0 &&
      length(google_project_iam_custom_role.reconciliation) == 0 && length(google_monitoring_alert_policy.reconciliation) == 0 &&
      length(output.reconciliation_pause_tasks) == 0 && google_cloud_scheduler_job.reconcile.paused &&
      alltrue([for job in google_cloud_scheduler_job.operator : job.paused])
    )
    error_message = "The default must add no gate, counter, timer identities, roles, alerts or enabled schedules."
  }
}

run "finite_gate_is_paused_and_debits_before_dispatch" {
  command = plan
  assert {
    condition = (
      google_cloud_scheduler_job.reconcile.paused && alltrue([for job in google_cloud_scheduler_job.operator : job.paused]) &&
      google_storage_bucket_object.reconciliation_budget[0].metadata.remaining == "8" &&
      google_storage_bucket_object.reconciliation_budget[0].metadata.window == var.budget_window_id &&
      google_storage_bucket_object.reconciliation_budget[0].metadata.deadline == var.finite_reconciliation.expires_at
    )
    error_message = "Provisioning the gate must keep schedules paused and initialize only the reviewed finite counter."
  }
  assert {
    condition = (
      yamldecode(google_workflows_workflow.reconciliation[0].source_contents).main.steps[2].check_markers.switch[0].next == "idle" &&
      yamldecode(google_workflows_workflow.reconciliation[0].source_contents).main.steps[5].consume_permit.call == "http.patch" &&
      keys(yamldecode(google_workflows_workflow.reconciliation[0].source_contents).main.steps[5].consume_permit.args.query) == ["ifGenerationMatch", "ifMetagenerationMatch"] &&
      yamldecode(google_workflows_workflow.reconciliation[0].source_contents).main.steps[7].dispatch_once.call == "http.post" &&
      yamldecode(google_workflows_workflow.reconciliation[0].source_contents).main.steps[9].idle.return.dispatcher_invocations == 0 &&
      !strcontains(google_workflows_workflow.reconciliation[0].source_contents, "retry:") &&
      !strcontains(google_workflows_workflow.reconciliation[0].source_contents, "next: consume_permit")
    )
    error_message = "A complete empty listing must return before counter/database/Run calls; nonempty work needs one atomic debit before a non-retried dispatch."
  }
}

run "roles_restrict_resources_operations_and_time" {
  command = plan
  assert {
    condition = (
      google_project_iam_custom_role.reconciliation["counter"].permissions == toset(["storage.objects.get", "storage.objects.update"]) &&
      google_project_iam_custom_role.reconciliation["list"].permissions == toset(["storage.objects.list"]) &&
      google_project_iam_custom_role.reconciliation["execute"].permissions == toset(["workflows.executions.create"]) &&
      google_project_iam_custom_role.reconciliation["pause"].permissions == toset(["cloudscheduler.jobs.pause"]) &&
      google_project_iam_custom_role.reconciliation["logs"].permissions == toset(["logging.logEntries.create"]) &&
      strcontains(google_storage_bucket_iam_member.reconciliation_counter[0].condition[0].expression,
      "resource.name == \"projects/_/buckets/research-offline-fixture-research-staging-dispatch/objects/reconciliation-budget/offline-reviewed-fixture\"") &&
      google_project_iam_member.reconciliation_execute[0].condition[0].expression == local.reconciliation_window_condition.expression &&
      google_cloud_run_v2_service_iam_member.reconciliation_dispatch[0].condition[0].expression == local.reconciliation_window_condition.expression
    )
    error_message = "Gate access must use exact-object conditional get/update, list-only hints, deadline-limited execution creation and pause-only cleanup."
  }
}

run "gate_routes_schedules_without_unconditional_invocation_bypass" {
  command = plan
  assert {
    condition = (
      google_cloud_scheduler_job.reconcile.http_target[0].uri == local.reconciliation_execution_uri &&
      length(google_cloud_scheduler_job.reconcile.http_target[0].oidc_token) == 0 &&
      length(google_cloud_scheduler_job.reconcile.http_target[0].oauth_token) == 1 &&
      keys(google_cloud_run_v2_service_iam_member.dispatch) == ["tasks"] &&
      google_cloud_run_v2_service_iam_member.dispatch["tasks"].condition[0].expression == local.reconciliation_deadline_condition.expression &&
      alltrue([for grant in google_cloud_run_v2_job_iam_member.operator_schedule :
      grant.condition[0].expression == local.reconciliation_window_condition.expression]) &&
      google_cloud_tasks_queue.dispatch.retry_config[0].max_attempts == 5 &&
      google_cloud_tasks_queue.dispatch.retry_config[0].max_retry_duration == "0s"
    )
    error_message = "The Scheduler must use the Workflow gate; its direct Run grant is absent, task/operator grants expire and task deliveries stay finite."
  }
}

run "exactly_four_one_time_pause_payloads" {
  command = plan
  assert {
    condition = (
      length(output.reconciliation_pause_tasks) == 4 &&
      alltrue([for item in output.reconciliation_pause_tasks :
        item.task.scheduleTime == var.finite_reconciliation.expires_at &&
        item.task.httpRequest.httpMethod == "POST" &&
        item.task.retryConfig.maxAttempts == 5 && item.task.retryConfig.minBackoff == "150s" &&
        item.task.retryConfig.maxBackoff == "150s" && item.task.retryConfig.maxRetryDuration == "600s" &&
        item.task.httpRequest.oauthToken.scope == "https://www.googleapis.com/auth/cloud-scheduler"
      ]) &&
      toset([for item in output.reconciliation_pause_tasks : item.task.httpRequest.url]) == toset([
        for job in ["reconcile", "backup", "prune", "cleanup"] :
        "https://cloudscheduler.googleapis.com/v1/projects/research-offline-fixture/locations/asia-southeast1/jobs/research-staging-${job}:pause"
      ])
    )
    error_message = "The timer handoff must contain exactly four one-time allowlisted pause calls, never an additional recurring schedule."
  }
}

run "refuse_missing_authoritative_window" {
  command = plan
  variables { budget_window_id = "" }
  expect_failures = [var.finite_reconciliation]
}

run "qualification_buffer_preserves_same_finite_allowances" {
  command = plan
  variables {
    finite_reconciliation = {
      starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T02:00:00Z", dispatch_limit = 8, accepted_queries = 2,
      other_service_cpu_seconds = 2260, other_service_gib_seconds = 1130, other_service_requests = 482, evidence_reference = "fixture"
    }
  }
  assert {
    condition = (
      google_storage_bucket_object.reconciliation_budget[0].metadata.remaining == "8" &&
      google_storage_bucket_object.reconciliation_budget[0].metadata.deadline == "2030-01-02T02:00:00Z" &&
      google_cloud_tasks_queue.dispatch.retry_config[0].max_attempts == 5 &&
      length(output.reconciliation_pause_tasks) == 4 &&
      alltrue([for item in output.reconciliation_pause_tasks : item.task.scheduleTime == "2030-01-02T02:00:00Z"]) &&
      google_cloud_scheduler_job.reconcile.paused && alltrue([for job in google_cloud_scheduler_job.operator : job.paused])
    )
    error_message = "The 26-hour qualification buffer must keep the same eight permits, five task attempts, four deadline pauses and paused initial schedules."
  }
}

run "refuse_overlong_rehearsal" {
  command = plan
  variables {
    finite_reconciliation = {
      starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T02:00:01Z", dispatch_limit = 4, accepted_queries = 2,
      other_service_cpu_seconds = 1000, other_service_gib_seconds = 500, other_service_requests = 330, evidence_reference = "fixture"
    }
  }
  expect_failures = [var.finite_reconciliation]
}

run "refuse_combined_task_and_gate_cpu_overcommit" {
  command = plan
  variables {
    finite_reconciliation = {
      starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T00:00:00Z", dispatch_limit = 8, accepted_queries = 2,
      other_service_cpu_seconds = 2261, other_service_gib_seconds = 500, other_service_requests = 330, evidence_reference = "fixture"
    }
  }
  expect_failures = [var.finite_reconciliation]
}

run "refuse_combined_memory_overcommit" {
  command = plan
  variables {
    finite_reconciliation = {
      starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T00:00:00Z", dispatch_limit = 8, accepted_queries = 2,
      other_service_cpu_seconds = 1000, other_service_gib_seconds = 1131, other_service_requests = 330, evidence_reference = "fixture"
    }
  }
  expect_failures = [var.finite_reconciliation]
}

run "refuse_combined_request_overcommit" {
  command = plan
  variables {
    finite_reconciliation = {
      starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T00:00:00Z", dispatch_limit = 8, accepted_queries = 2,
      other_service_cpu_seconds = 1000, other_service_gib_seconds = 500, other_service_requests = 483, evidence_reference = "fixture"
    }
  }
  expect_failures = [var.finite_reconciliation]
}

run "refuse_unbounded_gate_count" {
  command = plan
  variables {
    finite_reconciliation = {
      starts_at                 = "2030-01-01T00:00:00Z", expires_at = "2030-01-02T00:00:00Z", dispatch_limit = 16, accepted_queries = 2,
      other_service_cpu_seconds = 0, other_service_gib_seconds = 0, other_service_requests = 0, evidence_reference = "fixture"
    }
  }
  expect_failures = [var.finite_reconciliation]
}

run "refuse_schedule_before_timer_readback" {
  command = plan
  variables { enable_reconciliation = true }
  expect_failures = [var.finite_reconciliation]
}

run "refuse_unmonitored_gate" {
  command = plan
  variables { notification_channels = [] }
  expect_failures = [var.finite_reconciliation]
}
