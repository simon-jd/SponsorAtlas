# SOURCE ONLY: original mock1 consumed: 1 pass / 1 fail / 3 skipped; FAILED_STOP; no retry.
# Corrected fixture revision is UNEXECUTED.
# Mocking does not prove import/no-op actions, real permissions, monotonic phase
# transitions or the encrypted-state boundary. Those remain separate gates.
# NORMAL parity below means resource addresses/values/three attachments only:
# attachment prevent_destroy now deliberately also protects NORMAL instances.
mock_provider "snowflake" {}

variables {
  service_public_keys = {
    AIRBYTE_SVC = join("", [for _ in range(392) : "A"])
    DBT_SVC     = join("", [for _ in range(392) : "B"])
    CUBE_SVC    = join("", [for _ in range(392) : "C"])
  }
}

run "normal_default_addresses_and_values" {
  command = plan

  assert {
    condition = (
      var.native_phase == "NORMAL" && !local.native_bootstrap &&
      local.native_users_disabled == null &&
      local.native_provider_warehouse == null && local.native_resource_monitor == null &&
      length(local.native_warehouse_imports) == 0 && length(local.native_attachment_imports) == 0
    )
    error_message = "Default NORMAL must not opt into native connection, monitor, disabled or import settings."
  }
  assert {
    condition = (
      toset(keys(snowflake_service_user.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      toset(keys(snowflake_user_authentication_policy_attachment.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      length(snowflake_database.data) == 2 && length(snowflake_schema.data) == 7 &&
      length(snowflake_account_role.functional) == 4
    )
    error_message = "NORMAL must retain the original foundation and all three attachment instance keys."
  }
  assert {
    condition = alltrue([
      for name, user in snowflake_service_user.service :
      user.name == name && user.login_name == name &&
      user.rsa_public_key == var.service_public_keys[name] &&
      user.default_role == local.service_users[name] &&
      user.default_secondary_roles_option == "NONE" &&
      snowflake_user_authentication_policy_attachment.service[name].user_name == name
    ])
    error_message = "NORMAL must preserve the service/role/key bindings and secondary NONE."
  }
  assert {
    condition = (
      snowflake_warehouse.compute.name == "SPONSORATLAS_XS" &&
      snowflake_warehouse.compute.resource_monitor == null &&
      snowflake_warehouse.compute.warehouse_size == "XSMALL" &&
      snowflake_warehouse.compute.auto_suspend == 60 &&
      snowflake_warehouse.compute.initially_suspended
    )
    error_message = "NORMAL warehouse values must remain unchanged."
  }
}

run "create_disabled_roots_without_attachments" {
  override_resource {
    target = snowflake_warehouse.compute
  }

  command = plan
  variables {
    native_phase = "CREATE_DISABLED"
  }

  assert {
    condition = (
      toset(keys(snowflake_service_user.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      alltrue([for user in snowflake_service_user.service : user.disabled == "true"]) &&
      length(snowflake_user_authentication_policy_attachment.service) == 0 &&
      length(local.native_attachment_imports) == 0
    )
    error_message = "CREATE_DISABLED must retain all three users, disable them at creation and omit every attachment."
  }
  assert {
    condition = (
      toset(keys(snowflake_database.data)) == toset(["RAW", "ANALYTICS"]) &&
      toset(keys(snowflake_schema.data)) == toset([
        "raw_lca", "raw_uscis", "raw_bls", "raw_jobs",
        "analytics_staging", "analytics_intermediate", "analytics_marts"
      ]) &&
      toset(keys(snowflake_account_role.functional)) == toset(["LOADER", "TRANSFORMER", "ANALYST", "PII_READER"]) &&
      toset(keys(snowflake_grant_account_role.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      snowflake_authentication_policy.key_pair_only.authentication_methods == toset(["KEYPAIR"])
    )
    error_message = "CREATE_DISABLED must not filter roots, service-role grants or the KEYPAIR policy."
  }
  assert {
    condition = (
      local.native_provider_warehouse == "SPONSORATLAS_XS" &&
      snowflake_warehouse.compute.resource_monitor == "SPONSORATLAS_BOOTSTRAP_RM" &&
      length(local.native_warehouse_imports) == 1 &&
      local.native_warehouse_imports["compute"] == "\"SPONSORATLAS_XS\""
    )
    error_message = "Native connection/import must use only the precreated project XS with its preassigned monitor."
  }
}

run "import_attachments_while_disabled" {
  override_resource {
    target = snowflake_warehouse.compute
  }
  override_resource {
    target = snowflake_user_authentication_policy_attachment.service["AIRBYTE_SVC"]
  }
  override_resource {
    target = snowflake_user_authentication_policy_attachment.service["DBT_SVC"]
  }
  override_resource {
    target = snowflake_user_authentication_policy_attachment.service["CUBE_SVC"]
  }
  override_resource {
    target          = snowflake_authentication_policy.key_pair_only
    override_during = plan
    values = {
      fully_qualified_name = "\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\""
    }
  }

  command = plan
  variables {
    native_phase = "IMPORT_DISABLED"
  }

  assert {
    condition = (
      toset(keys(snowflake_service_user.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      alltrue([for user in snowflake_service_user.service : user.disabled == "true"]) &&
      toset(keys(snowflake_user_authentication_policy_attachment.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      toset(keys(local.native_attachment_imports)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"])
    )
    error_message = "IMPORT_DISABLED must import the original three attachment addresses without enabling users."
  }
  assert {
    condition = (
      local.native_attachment_imports["AIRBYTE_SVC"] == "\"AIRBYTE_SVC\"|\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\"" &&
      local.native_attachment_imports["DBT_SVC"] == "\"DBT_SVC\"|\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\"" &&
      local.native_attachment_imports["CUBE_SVC"] == "\"CUBE_SVC\"|\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\"" &&
      alltrue([for name, attachment in snowflake_user_authentication_policy_attachment.service : attachment.user_name == name])
    )
    error_message = "Attachment IDs must use the pinned quoted user|database.schema.policy grammar for exactly the intended users."
  }
  assert {
    condition = (
      length(snowflake_database.data) == 2 && length(snowflake_schema.data) == 7 &&
      length(snowflake_account_role.functional) == 4 &&
      local.native_provider_warehouse == "SPONSORATLAS_XS" &&
      snowflake_warehouse.compute.resource_monitor == "SPONSORATLAS_BOOTSTRAP_RM"
    )
    error_message = "Import phase must retain all foundation objects and the same connection/monitor."
  }
}

run "ready_retains_addresses_and_imports" {
  override_resource {
    target = snowflake_warehouse.compute
  }
  override_resource {
    target = snowflake_user_authentication_policy_attachment.service["AIRBYTE_SVC"]
  }
  override_resource {
    target = snowflake_user_authentication_policy_attachment.service["DBT_SVC"]
  }
  override_resource {
    target = snowflake_user_authentication_policy_attachment.service["CUBE_SVC"]
  }
  override_resource {
    target          = snowflake_authentication_policy.key_pair_only
    override_during = plan
    values = {
      fully_qualified_name = "\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\""
    }
  }

  command = plan
  variables {
    native_phase = "READY"
  }

  assert {
    condition = (
      toset(keys(snowflake_service_user.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      alltrue([for user in snowflake_service_user.service : user.disabled == "false"]) &&
      toset(keys(snowflake_user_authentication_policy_attachment.service)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      toset(keys(local.native_attachment_imports)) == toset(["AIRBYTE_SVC", "DBT_SVC", "CUBE_SVC"]) &&
      length(local.native_warehouse_imports) == 1
    )
    error_message = "READY must enable only the same three users and retain the existing attachment/import addresses."
  }
  assert {
    condition = alltrue([
      for name, user in snowflake_service_user.service :
      user.rsa_public_key == var.service_public_keys[name] &&
      user.default_role == local.service_users[name] &&
      user.default_secondary_roles_option == "NONE" &&
      snowflake_grant_account_role.service[name].user_name == name &&
      snowflake_user_authentication_policy_attachment.service[name].user_name == name &&
      local.native_attachment_imports[name] == format("\"%s\"|\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\"", name)
    ])
    error_message = "READY must not rotate keys, broaden roles or retarget any user/policy binding."
  }
  assert {
    condition = (
      length(snowflake_database.data) == 2 && length(snowflake_schema.data) == 7 &&
      length(snowflake_account_role.functional) == 4 &&
      local.native_provider_warehouse == "SPONSORATLAS_XS" &&
      snowflake_warehouse.compute.resource_monitor == "SPONSORATLAS_BOOTSTRAP_RM"
    )
    error_message = "READY must retain foundation, project warehouse and monitor."
  }
}

run "reject_unknown_native_phase" {
  command = plan
  variables {
    native_phase = "READY_AFTER_APPROVAL"
  }
  expect_failures = [var.native_phase]
}
