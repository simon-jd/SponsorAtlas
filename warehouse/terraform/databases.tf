locals {
  schemas = {
    raw_lca                = { database = "RAW", name = "LCA" }
    raw_uscis              = { database = "RAW", name = "USCIS" }
    raw_bls                = { database = "RAW", name = "BLS" }
    raw_jobs               = { database = "RAW", name = "JOBS" }
    analytics_staging      = { database = "ANALYTICS", name = "STAGING" }
    analytics_intermediate = { database = "ANALYTICS", name = "INTERMEDIATE" }
    analytics_marts        = { database = "ANALYTICS", name = "MARTS" }
  }
}

resource "snowflake_database" "data" {
  for_each = toset(["RAW", "ANALYTICS"])
  name     = each.key
  comment  = "SponsorAtlas nonproduction data foundation; deployment requires approval."
}

resource "snowflake_schema" "data" {
  for_each            = local.schemas
  database            = snowflake_database.data[each.value.database].name
  name                = each.value.name
  with_managed_access = "true"
  comment             = "Grants are centrally managed by the deployment role."
}
