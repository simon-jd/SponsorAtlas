resource "snowflake_warehouse" "compute" {
  name                                = "SPONSORATLAS_XS"
  warehouse_type                      = "STANDARD"
  warehouse_size                      = "XSMALL"
  auto_suspend                        = 60
  auto_resume                         = "true"
  initially_suspended                 = true
  # Human assignment must already be verified before any native provider read.
  resource_monitor                    = local.native_resource_monitor
  min_cluster_count                   = 1
  max_cluster_count                   = 1
  statement_timeout_in_seconds        = 300
  statement_queued_timeout_in_seconds = 60
  comment                             = "XS only; no apply without nonproduction budget authorization."
}
