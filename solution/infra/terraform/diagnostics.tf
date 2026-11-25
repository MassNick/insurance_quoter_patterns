# Resource logs and metrics of every platform service go to the Log Analytics workspace (architecture 13, 15).
locals {
  diagnostic_targets = {
    cosmos  = azurerm_cosmosdb_account.this["app"].id
    search  = azurerm_search_service.this.id
    kv      = azurerm_key_vault.this.id
    acr     = azurerm_container_registry.this.id
    foundry = module.foundry.account_id
    afd     = module.frontdoor.profile_id
    uploads = "${azurerm_storage_account.this["uploads"].id}/blobServices/default"
  }
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  for_each                   = local.diagnostic_targets
  name                       = "to-log-analytics"
  target_resource_id         = each.value
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log { category_group = "allLogs" }
  enabled_metric { category = "AllMetrics" }
}
