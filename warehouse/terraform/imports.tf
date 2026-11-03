# SOURCE ONLY. Import intent is not proof of object existence or authorization.
# The strict-create path imports the precreated warehouse, never the foundation
# databases, schemas, roles or users that Task1 requires Terraform to create.
locals {
  native_warehouse_imports = local.native_bootstrap ? { compute = "\"SPONSORATLAS_XS\"" } : {}
  native_attachment_imports = contains(["IMPORT_DISABLED", "READY"], var.native_phase) ? {
    for user in keys(local.service_users) :
    user => format("\"%s\"|\"ANALYTICS\".\"STAGING\".\"SERVICE_KEY_PAIR_ONLY\"", user)
  } : {}
}

import {
  for_each = local.native_warehouse_imports
  to       = snowflake_warehouse.compute
  id       = each.value
}

import {
  for_each = local.native_attachment_imports
  to       = snowflake_user_authentication_policy_attachment.service[each.key]
  id       = each.value
}
