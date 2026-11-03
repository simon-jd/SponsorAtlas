locals {
  roles = ["LOADER", "TRANSFORMER", "ANALYST", "PII_READER"]
  database_access = {
    LOADER_RAW            = { role = "LOADER", database = "RAW" }
    TRANSFORMER_RAW       = { role = "TRANSFORMER", database = "RAW" }
    TRANSFORMER_ANALYTICS = { role = "TRANSFORMER", database = "ANALYTICS" }
    ANALYST_ANALYTICS     = { role = "ANALYST", database = "ANALYTICS" }
  }
  schema_access = {
    LOADER_raw_lca                     = { role = "LOADER", schema = "raw_lca", privileges = ["USAGE", "CREATE TABLE", "CREATE STAGE", "CREATE FILE FORMAT"] }
    LOADER_raw_uscis                   = { role = "LOADER", schema = "raw_uscis", privileges = ["USAGE", "CREATE TABLE", "CREATE STAGE", "CREATE FILE FORMAT"] }
    LOADER_raw_bls                     = { role = "LOADER", schema = "raw_bls", privileges = ["USAGE", "CREATE TABLE", "CREATE STAGE", "CREATE FILE FORMAT"] }
    LOADER_raw_jobs                    = { role = "LOADER", schema = "raw_jobs", privileges = ["USAGE", "CREATE TABLE", "CREATE STAGE", "CREATE FILE FORMAT"] }
    TRANSFORMER_raw_lca                = { role = "TRANSFORMER", schema = "raw_lca", privileges = ["USAGE"] }
    TRANSFORMER_raw_uscis              = { role = "TRANSFORMER", schema = "raw_uscis", privileges = ["USAGE"] }
    TRANSFORMER_raw_bls                = { role = "TRANSFORMER", schema = "raw_bls", privileges = ["USAGE"] }
    TRANSFORMER_raw_jobs               = { role = "TRANSFORMER", schema = "raw_jobs", privileges = ["USAGE"] }
    TRANSFORMER_analytics_staging      = { role = "TRANSFORMER", schema = "analytics_staging", privileges = ["USAGE", "CREATE TABLE", "CREATE VIEW"] }
    TRANSFORMER_analytics_intermediate = { role = "TRANSFORMER", schema = "analytics_intermediate", privileges = ["USAGE", "CREATE TABLE", "CREATE VIEW"] }
    TRANSFORMER_analytics_marts        = { role = "TRANSFORMER", schema = "analytics_marts", privileges = ["USAGE", "CREATE TABLE", "CREATE VIEW"] }
    ANALYST_analytics_marts            = { role = "ANALYST", schema = "analytics_marts", privileges = ["USAGE"] }
  }
  object_access = {
    LOADER_raw_lca_TABLES                     = { role = "LOADER", schema = "raw_lca", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    LOADER_raw_uscis_TABLES                   = { role = "LOADER", schema = "raw_uscis", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    LOADER_raw_bls_TABLES                     = { role = "LOADER", schema = "raw_bls", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    LOADER_raw_jobs_TABLES                    = { role = "LOADER", schema = "raw_jobs", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    TRANSFORMER_raw_lca_TABLES                = { role = "TRANSFORMER", schema = "raw_lca", kind = "TABLES", privileges = ["SELECT"] }
    TRANSFORMER_raw_lca_VIEWS                 = { role = "TRANSFORMER", schema = "raw_lca", kind = "VIEWS", privileges = ["SELECT"] }
    TRANSFORMER_raw_uscis_TABLES              = { role = "TRANSFORMER", schema = "raw_uscis", kind = "TABLES", privileges = ["SELECT"] }
    TRANSFORMER_raw_uscis_VIEWS               = { role = "TRANSFORMER", schema = "raw_uscis", kind = "VIEWS", privileges = ["SELECT"] }
    TRANSFORMER_raw_bls_TABLES                = { role = "TRANSFORMER", schema = "raw_bls", kind = "TABLES", privileges = ["SELECT"] }
    TRANSFORMER_raw_bls_VIEWS                 = { role = "TRANSFORMER", schema = "raw_bls", kind = "VIEWS", privileges = ["SELECT"] }
    TRANSFORMER_raw_jobs_TABLES               = { role = "TRANSFORMER", schema = "raw_jobs", kind = "TABLES", privileges = ["SELECT"] }
    TRANSFORMER_raw_jobs_VIEWS                = { role = "TRANSFORMER", schema = "raw_jobs", kind = "VIEWS", privileges = ["SELECT"] }
    TRANSFORMER_analytics_staging_TABLES      = { role = "TRANSFORMER", schema = "analytics_staging", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    TRANSFORMER_analytics_staging_VIEWS       = { role = "TRANSFORMER", schema = "analytics_staging", kind = "VIEWS", privileges = ["SELECT"] }
    TRANSFORMER_analytics_intermediate_TABLES = { role = "TRANSFORMER", schema = "analytics_intermediate", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    TRANSFORMER_analytics_intermediate_VIEWS  = { role = "TRANSFORMER", schema = "analytics_intermediate", kind = "VIEWS", privileges = ["SELECT"] }
    TRANSFORMER_analytics_marts_TABLES        = { role = "TRANSFORMER", schema = "analytics_marts", kind = "TABLES", privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"] }
    TRANSFORMER_analytics_marts_VIEWS         = { role = "TRANSFORMER", schema = "analytics_marts", kind = "VIEWS", privileges = ["SELECT"] }
    ANALYST_analytics_marts_TABLES            = { role = "ANALYST", schema = "analytics_marts", kind = "TABLES", privileges = ["SELECT"] }
    ANALYST_analytics_marts_VIEWS             = { role = "ANALYST", schema = "analytics_marts", kind = "VIEWS", privileges = ["SELECT"] }
  }
}

resource "snowflake_account_role" "functional" {
  for_each = toset(local.roles)
  name     = each.key
  comment  = "SponsorAtlas functional role; PII_READER is reserved for later masking policies."
}

resource "snowflake_grant_privileges_to_account_role" "warehouse_usage" {
  with_grant_option = false
  for_each          = toset(["LOADER", "TRANSFORMER", "ANALYST"])
  account_role_name = snowflake_account_role.functional[each.key].name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.compute.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "database_usage" {
  with_grant_option = false
  for_each          = local.database_access
  account_role_name = snowflake_account_role.functional[each.value.role].name
  privileges        = ["USAGE"]
  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.data[each.value.database].name
  }
}

resource "snowflake_grant_privileges_to_account_role" "schema_access" {
  with_grant_option = false
  for_each          = local.schema_access
  account_role_name = snowflake_account_role.functional[each.value.role].name
  privileges        = each.value.privileges
  on_schema {
    schema_name = snowflake_schema.data[each.value.schema].fully_qualified_name
  }
}

# Both current and future objects need grants; no blanket database-wide SELECT.
resource "snowflake_grant_privileges_to_account_role" "existing_objects" {
  with_grant_option = false
  for_each          = local.object_access
  account_role_name = snowflake_account_role.functional[each.value.role].name
  privileges        = each.value.privileges
  on_schema_object {
    all {
      object_type_plural = each.value.kind
      in_schema          = snowflake_schema.data[each.value.schema].fully_qualified_name
    }
  }
}

resource "snowflake_grant_privileges_to_account_role" "future_objects" {
  with_grant_option = false
  for_each          = local.object_access
  account_role_name = snowflake_account_role.functional[each.value.role].name
  privileges        = each.value.privileges
  on_schema_object {
    future {
      object_type_plural = each.value.kind
      in_schema          = snowflake_schema.data[each.value.schema].fully_qualified_name
    }
  }
}

# Object-scoped APPLY only. This permits creation-time binding, not policy editing.
locals {
  governance_bindings = {
    owner       = { kind = "ROW ACCESS POLICY", name = snowflake_row_access_policy.application_owner.fully_qualified_name }
    email       = { kind = "MASKING POLICY", name = snowflake_masking_policy.contact_email.fully_qualified_name }
    pii         = { kind = "TAG", name = snowflake_tag.pii.fully_qualified_name }
    sensitivity = { kind = "TAG", name = snowflake_tag.sensitivity.fully_qualified_name }
  }
}
resource "snowflake_grant_privileges_to_account_role" "governance_binding" {
  for_each          = local.governance_bindings
  account_role_name = snowflake_account_role.functional["TRANSFORMER"].name
  privileges        = ["APPLY"]
  with_grant_option = false
  on_schema_object {
    object_type = each.value.kind
    object_name = each.value.name
  }
}

variable "application_owner_roles" {
  description = "Explicit approved owner roles; no guessed identities or automatic grants to service users. Keep dbt approved_application_owner_roles identical."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for role in var.application_owner_roles : can(regex("^SPONSORATLAS_OWNER_[A-Z0-9_]+$", role))])
    error_message = "Owner roles must use the uppercase SPONSORATLAS_OWNER_ namespace."
  }
}
resource "snowflake_account_role" "application_owner" {
  for_each = var.application_owner_roles
  name     = each.value
  comment  = "Explicit application owner role. A separate reviewed identity grant is required."
}
