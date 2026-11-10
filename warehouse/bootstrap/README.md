# Manual Snowflake bootstrap

`native-bootstrap.sql` is an inert, comment-only sketch of the steps a human account admin would
take before the first `terraform apply`: check the account for name collisions and existing
grants, create a project-only resource monitor, create the suspended X-Small warehouse with that
monitor, and grant a temporary deployment role only what the apply needs. The Terraform variable
`native_phase` (`NORMAL`, `CREATE_DISABLED`, `IMPORT_DISABLED`, `READY`) stages the apply so the
service users stay disabled until their key-pair policy is attached. The SQL file is comment-only
and was not executed. The resource monitor `SPONSORATLAS_BOOTSTRAP_RM` and the warehouse were
created by hand, and the warehouse was imported during the 2026-10-30 apply, which went through
CREATE_DISABLED, IMPORT_DISABLED and READY (logs in `.tools/terraform/plans/`, not in git).
`warehouse/terraform/tests/native_bootstrap.tftest.hcl` covers the phases with a mock provider.
The full runbook is in [docs/dev/warehouse-bootstrap.md](../../docs/dev/warehouse-bootstrap.md).
