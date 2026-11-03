resource "airbyte_connection" "file" {
  for_each                             = var.file_releases
  name                                 = "${each.key} to RAW.${each.value.schema}"
  source_id                            = airbyte_source.file[each.key].source_id
  destination_id                       = airbyte_destination.raw[each.value.schema].destination_id
  namespace_definition                 = "destination"
  non_breaking_schema_updates_behavior = "disable_connection"
  schedule                             = { schedule_type = "manual" }
  configurations = { streams = [{
    name = each.value.stream, sync_mode = "full_refresh_overwrite"
  }] }
}
resource "airbyte_connection" "board" {
  for_each                             = var.boards
  name                                 = "${each.key} public board to RAW.JOBS"
  source_id                            = airbyte_source.board[each.key].source_id
  destination_id                       = airbyte_destination.raw["JOBS"].destination_id
  namespace_definition                 = "destination"
  prefix                               = "${upper(each.key)}_"
  non_breaking_schema_updates_behavior = "disable_connection"
  schedule                             = { schedule_type = "manual" }
  configurations = { streams = [{
    name = each.value.provider, sync_mode = "full_refresh_overwrite", primary_key = [["job_key"]]
  }] }
}
resource "airbyte_connection" "cdc" {
  name                                 = "Jobs replica pgoutput CDC to RAW.JOBS"
  source_id                            = airbyte_source.postgres.source_id
  destination_id                       = airbyte_destination.raw["JOBS"].destination_id
  namespace_definition                 = "destination"
  prefix                               = "CDC_"
  non_breaking_schema_updates_behavior = "disable_connection"
  # Airbyte Quartz cron, UTC. Schedule is only a target; <15m remains a measured gate.
  schedule = { schedule_type = "cron", cron_expression = "0 0/5 * * * ?" }
  configurations = { streams = [for table in ["job_postings", "application_events"] : {
    name = table, namespace = "public", sync_mode = "incremental_deduped_history", primary_key = [["id"]]
  }] }
}
output "raw_tables" {
  value = {
    files  = { for k, v in var.file_releases : k => "RAW.${v.schema}.${v.stream}" }
    boards = { for k, v in var.boards : k => "RAW.JOBS.${upper(k)}_${upper(v.provider)}" }
    cdc    = ["RAW.JOBS.CDC_JOB_POSTINGS", "RAW.JOBS.CDC_APPLICATION_EVENTS"]
  }
}
output "connection_ids" {
  value = {
    files  = { for k, v in airbyte_connection.file : k => v.connection_id }
    boards = { for k, v in airbyte_connection.board : k => v.connection_id }
    cdc    = airbyte_connection.cdc.connection_id
  }
}
