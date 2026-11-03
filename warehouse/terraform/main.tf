terraform {
  required_version = ">= 1.11.0, < 2.0.0"
  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "= 2.22.0"
    }
  }
}

# Credentials come from the provider's SNOWFLAKE_* environment variables.
# Explicit JWT prevents falling back to password authentication.
provider "snowflake" {
  authenticator = "SNOWFLAKE_JWT"
  warehouse     = local.native_provider_warehouse
}

variable "native_phase" {
  description = "SOURCE-only bootstrap wiring; selecting a phase is not permission to run it. NORMAL preserves the original configuration. Native transitions require separate review."
  type        = string
  default     = "NORMAL"
  nullable    = false
  validation {
    condition     = contains(["NORMAL", "CREATE_DISABLED", "IMPORT_DISABLED", "READY"], var.native_phase)
    error_message = "native_phase must be NORMAL, CREATE_DISABLED, IMPORT_DISABLED or READY."
  }
}

locals {
  native_bootstrap          = var.native_phase != "NORMAL"
  native_provider_warehouse = local.native_bootstrap ? "SPONSORATLAS_XS" : null
  native_resource_monitor   = local.native_bootstrap ? "SPONSORATLAS_BOOTSTRAP_RM" : null
  # The pinned provider expects boolean strings; null retains its original default.
  native_users_disabled = var.native_phase == "NORMAL" ? null : (var.native_phase == "READY" ? "false" : "true")
}

variable "service_public_keys" {
  description = "RSA public keys, PEM headers/footers removed; never supply private keys."
  type        = map(string)
  validation {
    condition = (
      toset(keys(var.service_public_keys)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      alltrue([for key in values(var.service_public_keys) : can(regex("^[A-Za-z0-9+/=]{300,}$", key))])
    )
    error_message = "Provide exactly AIRBYTE_SVC, DBT_SVC, CUBE_SVC with base64 DER RSA public keys."
  }
}
