variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "asia-southeast1"
}
variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Use separate staging and production state and database targets."
  }
}
variable "app_commit" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.app_commit))
    error_message = "Record the complete reviewed source commit."
  }
}
variable "images" {
  type = object({ service = string, operator = string })
  validation {
    condition     = alltrue([for image in values(var.images) : can(regex("@sha256:[0-9a-f]{64}$", image))])
    error_message = "Both Linux amd64 images must be pinned by digest."
  }
}
variable "review" {
  type = object({
    experiment_limit_usd = number
    monthly_limit_usd    = number
    provider_plan        = string
    database_region      = string
    quote_date           = string
    evidence_reference   = string
    approved             = bool
  })
  description = "Reviewed all-provider monthly ceiling below USD 5; experiments share that ceiling. This module's Google budget is alert-only, not a spend-cap budget."
  validation {
    condition = (var.review.approved &&
      length(var.review.provider_plan) > 3 && length(var.review.database_region) > 3 && length(var.review.evidence_reference) > 3 &&
    can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.review.quote_date)))
    error_message = "Provisioning requires reviewed provider evidence and an explicit spending decision."
  }
  validation {
    condition = (var.review.monthly_limit_usd > 0 && var.review.monthly_limit_usd < 5 &&
      floor(var.review.monthly_limit_usd * 100) == var.review.monthly_limit_usd * 100 &&
      var.review.experiment_limit_usd >= 0 && var.review.experiment_limit_usd <= var.review.monthly_limit_usd &&
    floor(var.review.experiment_limit_usd * 100) == var.review.experiment_limit_usd * 100)
    error_message = "Use whole USD cents with 0 < monthly limit < 5; the nonnegative experiment allocation must fit inside that same monthly ceiling."
  }
}
variable "secret_versions" {
  type        = map(string)
  description = "Numeric Secret Manager versions; values never enter Terraform. Populate secrets after the bootstrap target."
  validation {
    condition     = alltrue([for version in values(var.secret_versions) : can(regex("^[1-9][0-9]*$", version))])
    error_message = "Use immutable numeric secret versions, never latest."
  }
}
variable "allowed_origins" {
  type    = list(string)
  default = ["https://haroldng.dev"]
  validation {
    condition     = length(var.allowed_origins) > 0 && alltrue([for origin in var.allowed_origins : can(regex("^https://[^/]+$", origin))])
    error_message = "Supply explicit HTTPS browser origins."
  }
}
variable "public_api" {
  type    = bool
  default = false
}
variable "rehearsal_invokers" {
  type    = set(string)
  default = []
}
variable "enable_reconciliation" {
  type    = bool
  default = false
}
variable "enable_operator_schedules" {
  type    = bool
  default = false
}
variable "recovery_and_alert_evidence" {
  type    = string
  default = ""
}
variable "notification_channels" {
  type        = list(string)
  default     = []
  description = "Existing verified Monitoring channel resource names; record recipient and delivery evidence in handoff."
}
variable "recovery_principal" {
  type        = string
  description = "Independently authenticated operator IAM member; never a runtime service account."
}
variable "billing_account" {
  type        = string
  description = "Billing account ID for project-scoped alerts. This does not enforce a spending cap."
}
variable "google_alert_budget" {
  type = object({
    currency_code  = string
    amount         = number
    usd_equivalent = number
  })
  default     = null
  description = "Google alert amount in the billing account currency (supported: USD/HKD), with its reviewed USD equivalent inside review.monthly_limit_usd, not an additional allowance. Record currency/FX evidence under review.evidence_reference and review.quote_date. Null derives the existing USD alert from the monthly ceiling and is valid only for a USD billing account."
  validation {
    condition = var.google_alert_budget == null ? true : try(
      contains(["USD", "HKD"], var.google_alert_budget.currency_code) &&
      var.google_alert_budget.amount > 0 &&
      floor(var.google_alert_budget.amount * 100) == var.google_alert_budget.amount * 100,
    false)
    error_message = "Use the billing account's supported currency code (USD or HKD) and a positive alert amount in whole cents."
  }
  validation {
    condition = var.google_alert_budget == null ? true : try(
      var.google_alert_budget.usd_equivalent > 0 &&
      var.google_alert_budget.usd_equivalent <= var.review.monthly_limit_usd &&
      var.google_alert_budget.usd_equivalent < 5 &&
      (var.google_alert_budget.currency_code != "USD" || var.google_alert_budget.amount == var.google_alert_budget.usd_equivalent),
    false)
    error_message = "The reviewed USD equivalent must be positive and fit inside the same all-provider monthly ceiling below USD 5; a USD alert must equal its USD equivalent."
  }
}
variable "operator_staging_bytes" {
  type    = number
  default = 2147483648
  validation {
    condition     = var.operator_staging_bytes > 0 && var.operator_staging_bytes <= 2147483648 && floor(var.operator_staging_bytes) == var.operator_staging_bytes
    error_message = "With a 4 GiB operator, staging must be a positive integer no larger than 2 GiB; preserve process memory."
  }
}
variable "operator_dump_bytes" {
  type    = number
  default = 1073741824
  validation {
    condition     = var.operator_dump_bytes > 0 && var.operator_dump_bytes + 16777216 < var.operator_staging_bytes && floor(var.operator_dump_bytes) == var.operator_dump_bytes
    error_message = "Dump capacity must be a positive integer and leave more than 16 MiB within staging; object capacity is also checked at runtime."
  }
}
variable "r2_namespaces" {
  type = object({ objects = string, backups = string, reports = string })
  validation {
    condition     = length(toset(values(var.r2_namespaces))) == 3 && alltrue([for prefix in values(var.r2_namespaces) : can(regex("^(staging|production)/[a-z0-9/-]+/$", prefix)) && startswith(prefix, "${var.environment}/")])
    error_message = "Use three distinct environment-qualified object, backup and report prefixes."
  }
}
