# Versioned policy names: create v2, atomically rebuild all consumers with v2,
# verify POLICY_REFERENCES, then explicitly retire v1. Never detach to rebuild.
resource "snowflake_row_access_policy" "application_owner" {
  database = snowflake_database.data["ANALYTICS"].name
  schema   = snowflake_schema.data["analytics_marts"].name
  name     = "APPLICATION_OWNER_V1"
  argument {
    name = "owner_role"
    type = "VARCHAR"
  }
  body = "COALESCE(REGEXP_LIKE(owner_role, 'SPONSORATLAS_OWNER_[A-Z0-9_]+') AND IS_ROLE_IN_SESSION(owner_role), FALSE)"
  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_masking_policy" "contact_email" {
  database = snowflake_database.data["ANALYTICS"].name
  schema   = snowflake_schema.data["analytics_marts"].name
  name     = "CONTACT_EMAIL_V1"
  argument {
    name = "email"
    type = "VARCHAR"
  }
  return_data_type = "VARCHAR"
  body             = "CASE WHEN email IS NULL THEN NULL WHEN IS_ROLE_IN_SESSION('PII_READER') THEN email ELSE '***MASKED***' END"
  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_tag" "pii" {
  database               = snowflake_database.data["ANALYTICS"].name
  schema                 = snowflake_schema.data["analytics_marts"].name
  name                   = "PII"
  ordered_allowed_values = ["true", "false"]
}

resource "snowflake_tag" "sensitivity" {
  database               = snowflake_database.data["ANALYTICS"].name
  schema                 = snowflake_schema.data["analytics_marts"].name
  name                   = "SENSITIVITY"
  ordered_allowed_values = ["public", "restricted"]
}
