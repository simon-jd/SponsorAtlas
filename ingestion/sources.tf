# Custom connector definitions pin the actual runtime image, rather than relying
# on a globally mutable catalog default. Self-managed Airbyte API supports these.
resource "airbyte_source_definition" "file" {
  name              = "SponsorAtlas file 0.6.0"
  docker_repository = "airbyte/source-file"
  docker_image_tag  = "0.6.0"
  workspace_id      = var.workspace_id
  documentation_url = "https://docs.airbyte.com/integrations/sources/file"
}
resource "airbyte_source_definition" "postgres" {
  name              = "SponsorAtlas Postgres 3.8.9"
  docker_repository = "airbyte/source-postgres"
  docker_image_tag  = "3.8.9"
  workspace_id      = var.workspace_id
  documentation_url = "https://docs.airbyte.com/integrations/sources/postgres"
}
resource "airbyte_destination_definition" "snowflake" {
  name              = "SponsorAtlas Snowflake 5.2.0"
  docker_repository = "airbyte/destination-snowflake"
  docker_image_tag  = "5.2.0"
  workspace_id      = var.workspace_id
  documentation_url = "https://docs.airbyte.com/integrations/destinations/snowflake"
}
locals {
  board_manifest = yamldecode(file("${path.module}/board_api/manifest.yaml"))
}
# A separate definition per API makes discovery/check independent: a company's
# token on one platform must not cause requests to that name on other platforms.
resource "airbyte_declarative_source_definition" "board" {
  for_each     = toset(["greenhouse", "lever", "ashby"])
  name         = "SponsorAtlas ${each.key} public board"
  workspace_id = var.workspace_id
  manifest = jsonencode(merge(local.board_manifest, {
    streams = [for s in local.board_manifest.streams : s if s.name == each.key]
    check   = { type = "CheckStream", stream_names = [each.key] }
  }))
}
resource "airbyte_source" "file" {
  for_each      = var.file_releases
  name          = "SponsorAtlas ${each.key}"
  workspace_id  = var.workspace_id
  definition_id = airbyte_source_definition.file.id
  configuration = jsonencode({
    dataset_name = each.value.stream, format = "jsonl", url = each.value.url,
    provider     = { storage = "HTTPS" }, reader_options = jsonencode({ dtype = false, convert_dates = false })
  })
}
resource "airbyte_source" "postgres" {
  name          = "SponsorAtlas jobs replica CDC"
  workspace_id  = var.workspace_id
  definition_id = airbyte_source_definition.postgres.id
  configuration = jsonencode({
    host          = var.postgres.host, port = var.postgres.port, database = var.postgres.database,
    username      = var.postgres.username, password = var.postgres.password, schemas = ["public"],
    ssl_mode      = { mode = "verify-full", ca_certificate = var.postgres.ca_certificate },
    tunnel_method = { tunnel_method = "NO_TUNNEL" },
    replication_method = {
      method                  = "CDC", replication_slot = "sponsoratlas_airbyte", publication = "sponsoratlas_publication",
      initial_waiting_seconds = 120, lsn_commit_behaviour = "After loading Data in the destination"
    }
  })
}
resource "airbyte_source" "board" {
  for_each      = var.boards
  name          = "SponsorAtlas public ${each.key}"
  workspace_id  = var.workspace_id
  definition_id = airbyte_declarative_source_definition.board[each.value.provider].id
  configuration = jsonencode({ board = each.value.board })
}
# Task1 creates RAW schemas, AIRBYTE_SVC, LOADER, and SPONSORATLAS_XS.
# The existing warehouse remains XS/auto_suspend=60; ingestion creates no compute.
resource "airbyte_destination" "raw" {
  for_each      = toset(["LCA", "USCIS", "BLS", "JOBS"])
  name          = "SponsorAtlas RAW.${each.key}"
  workspace_id  = var.workspace_id
  definition_id = airbyte_destination_definition.snowflake.id
  configuration = jsonencode({
    host      = var.snowflake_host, database = "RAW", schema = each.key, raw_data_schema = each.key,
    warehouse = "SPONSORATLAS_XS", role = "LOADER", username = "AIRBYTE_SVC",
    credentials = {
      auth_type            = "Key Pair Authentication", private_key = var.snowflake_private_key,
      private_key_password = var.snowflake_private_key_passphrase
    },
    disable_type_dedupe   = false, cdc_deletion_mode = "Soft delete", trim_space = false,
    retention_period_days = 0, number_data_type = "NUMBER(38,9)"
  })
}
