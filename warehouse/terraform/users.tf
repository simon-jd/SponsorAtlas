locals {
  service_users = {
    AIRBYTE_SVC = "LOADER"
    DBT_SVC     = "TRANSFORMER"
    CUBE_SVC    = "ANALYST"
  }
  # No attachment resource is created before the human-only policy binding step.
  service_policy_users = var.native_phase == "CREATE_DISABLED" ? {} : local.service_users
}

resource "snowflake_service_user" "service" {
  for_each                       = local.service_users
  name                           = each.key
  login_name                     = each.key
  rsa_public_key                 = var.service_public_keys[each.key]
  default_role                   = snowflake_account_role.functional[each.value].name
  default_secondary_roles_option = "NONE"
  default_warehouse              = snowflake_warehouse.compute.name
  disabled                       = local.native_users_disabled
  comment                        = "SponsorAtlas service identity; RSA key-pair only."
}

resource "snowflake_grant_account_role" "service" {
  for_each  = local.service_users
  role_name = snowflake_account_role.functional[each.value].name
  user_name = snowflake_service_user.service[each.key].name
}

# SERVICE blocks passwords, and the policy also excludes OAuth, PAT and WIF.
resource "snowflake_authentication_policy" "key_pair_only" {
  database               = snowflake_database.data["ANALYTICS"].name
  schema                 = snowflake_schema.data["analytics_staging"].name
  name                   = "SERVICE_KEY_PAIR_ONLY"
  authentication_methods = ["KEYPAIR"]
  comment                = "Only RSA key-pair authentication for SponsorAtlas services."
}

resource "snowflake_user_authentication_policy_attachment" "service" {
  for_each                   = local.service_policy_users
  user_name                  = snowflake_service_user.service[each.key].name
  authentication_policy_name = snowflake_authentication_policy.key_pair_only.fully_qualified_name
  # A phase regression must not unset a live policy. This does not prevent FORCE
  # updates; all native attachment mutations still require rejection at review.
  lifecycle {
    prevent_destroy = true
  }
}
