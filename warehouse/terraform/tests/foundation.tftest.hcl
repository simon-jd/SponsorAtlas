# Terraform's mock provider evaluates the resource graph without any cloud calls.
mock_provider "snowflake" {}

variables {
  # Synthetic public-key-shaped values; no private credentials in offline tests.
  service_public_keys = {
    AIRBYTE_SVC = join("", [for _ in range(392) : "A"])
    DBT_SVC     = join("", [for _ in range(392) : "B"])
    CUBE_SVC    = join("", [for _ in range(392) : "C"])
  }
}

run "security_contracts" {
  command = plan

  assert {
    condition = (
      length(snowflake_database.data) == 2 && length(snowflake_schema.data) == 7 &&
      length(snowflake_account_role.functional) == 4 && length(snowflake_service_user.service) == 3
    )
    error_message = "Expected exactly 2 databases, 7 schemas, 4 functional roles and 3 service users."
  }
  assert {
    condition = (
      snowflake_warehouse.compute.warehouse_size == "XSMALL" &&
      snowflake_warehouse.compute.auto_suspend == 60 &&
      snowflake_warehouse.compute.initially_suspended &&
      snowflake_warehouse.compute.min_cluster_count == 1 &&
      snowflake_warehouse.compute.max_cluster_count == 1
    )
    error_message = "Warehouse must remain initially suspended, single-cluster XS, auto_suspend=60."
  }
  assert {
    condition = alltrue([
      for key, grant in snowflake_grant_privileges_to_account_role.schema_access :
      grant.account_role_name == local.schema_access[key].role &&
      grant.privileges == toset(local.schema_access[key].privileges) &&
      !grant.with_grant_option && grant.all_privileges != true
    ])
    error_message = "Schema grants must use the reviewed permission matrix without delegation."
  }
  assert {
    condition = alltrue(concat(
      [for key, grant in snowflake_grant_privileges_to_account_role.existing_objects :
        grant.account_role_name == local.object_access[key].role &&
        grant.privileges == toset(local.object_access[key].privileges) &&
        !grant.with_grant_option && grant.all_privileges != true
      ],
      [for key, grant in snowflake_grant_privileges_to_account_role.future_objects :
        grant.account_role_name == local.object_access[key].role &&
        grant.privileges == toset(local.object_access[key].privileges) &&
        !grant.with_grant_option && grant.all_privileges != true
      ]
    ))
    error_message = "Both current and future objects must match the permission matrix."
  }
  assert {
    condition = alltrue([
      for name, user in snowflake_service_user.service :
      user.name == name && user.login_name == name &&
      user.default_role == local.service_users[name] &&
      user.default_secondary_roles_option == "NONE" &&
      user.rsa_public_key == var.service_public_keys[name] &&
      snowflake_grant_account_role.service[name].role_name == local.service_users[name] &&
      snowflake_grant_account_role.service[name].user_name == name
    ])
    error_message = "Services must bind to exactly their intended role and RSA key."
  }
  assert {
    condition     = snowflake_authentication_policy.key_pair_only.authentication_methods == toset(["KEYPAIR"])
    error_message = "Service authentication policy must permit only KEYPAIR."
  }
}

run "reject_missing_service_key" {
  command = plan
  variables {
    service_public_keys = { AIRBYTE_SVC = join("", [for _ in range(392) : "A"]), DBT_SVC = join("", [for _ in range(392) : "B"]) }
  }
  expect_failures = [var.service_public_keys]
}

run "reject_extra_service_key" {
  command = plan
  variables {
    service_public_keys = {
      AIRBYTE_SVC = join("", [for _ in range(392) : "A"]), DBT_SVC = join("", [for _ in range(392) : "B"]),
      CUBE_SVC    = join("", [for _ in range(392) : "C"]), EXTRA_SVC = join("", [for _ in range(392) : "D"])
    }
  }
  expect_failures = [var.service_public_keys]
}

run "reject_private_pem_input" {
  command = plan
  variables {
    service_public_keys = {
      AIRBYTE_SVC = "-----BEGIN PRIVATE KEY-----",
      DBT_SVC     = join("", [for _ in range(392) : "B"]), CUBE_SVC = join("", [for _ in range(392) : "C"])
    }
  }
  expect_failures = [var.service_public_keys]
}
