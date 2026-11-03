# Offline provider mocks: no API calls, Snowflake queries, or resource creation.
mock_provider "airbyte" {}
variables {
  airbyte_client_id     = "synthetic-client"
  airbyte_client_secret = "synthetic-secret"
  workspace_id          = "00000000-0000-0000-0000-000000000001"
  snowflake_host        = "synthetic.snowflakecomputing.com"
  snowflake_private_key = "SYNTHETIC_NOT_A_PRIVATE_KEY"
  postgres = {
    host = "synthetic.example", database = "fixture", username = "airbyte_replication",
    password = "synthetic-only", ca_certificate = "synthetic-ca"
  }
  boards = { acme_gh = { provider = "greenhouse", board = "acme" } }
  file_releases = {
    lca_fy2024_q4 = {
      schema = "LCA", stream = "LCA_FY2024_Q4",
      url = "https://archive.example/LCA_FY2024_Q4__aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jsonl"
    }
  }
}
run "offline_plan" {
  command = plan
  assert {
    condition     = output.raw_tables.boards.acme_gh == "RAW.JOBS.ACME_GH_GREENHOUSE"
    error_message = "Board RAW table interface changed."
  }
  assert {
    condition     = jsondecode(airbyte_destination.raw["JOBS"].configuration).credentials.auth_type == "Key Pair Authentication"
    error_message = "Snowflake must use key-pair authentication."
  }
  assert {
    condition     = jsondecode(airbyte_source.postgres.configuration).replication_method.initial_waiting_seconds == 120
    error_message = "CDC wait must leave time for the 15-minute observed gate."
  }
}
