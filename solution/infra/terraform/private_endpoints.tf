# One private-endpoint pattern for every data service, driven by a map (architecture 12).
locals {
  private_endpoints = merge(
    { for k, a in azurerm_cosmosdb_account.this : "cosmos-${k}" => { id = a.id, group = "Sql", zones = ["cosmos"] } },
    { for k, a in azurerm_storage_account.this : "st-${k}" => { id = a.id, group = "blob", zones = ["blob"] } },
    {
      search  = { id = azurerm_search_service.this.id, group = "searchService", zones = ["search"] }
      kv      = { id = azurerm_key_vault.this.id, group = "vault", zones = ["vault"] }
      acr     = { id = azurerm_container_registry.this.id, group = "registry", zones = ["acr"] }
      foundry = { id = module.foundry.account_id, group = "account", zones = ["cognitiveservices", "openai", "aiservices"] }
    },
  )
}

resource "azurerm_private_endpoint" "this" {
  for_each            = local.private_endpoints
  name                = "pe-${each.key}-${local.suffix}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = module.network.subnet_ids["private-endpoints"]
  tags                = local.tags

  private_service_connection {
    name                           = each.key
    private_connection_resource_id = each.value.id
    subresource_names              = [each.value.group]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [for z in each.value.zones : module.network.dns_zone_ids[z]]
  }
}
