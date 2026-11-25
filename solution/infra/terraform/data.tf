# Two Cosmos accounts: the app's (database quoting) and the Agent Service's own state (BYO, created empty: the platform fills it).
# Three storage accounts: kb-docs (knowledge base source), uploads (customer documents) and the Agent Service's BYO files.
locals {
  cosmos_accounts = {
    app     = { name = local.name.cosmos["app"], failover = var.cosmos_failover_location }
    foundry = { name = local.name.cosmos["foundry"], failover = null }
  }
  storage_accounts = {
    kb      = ["kb-docs"]
    uploads = ["uploads"]
    foundry = []
  }
  storage_containers = { for p in flatten([for a, cs in local.storage_accounts : [for c in cs : { account = a, name = c }]]) : "${p.account}/${p.name}" => p }
}

resource "azurerm_cosmosdb_account" "this" {
  for_each                           = local.cosmos_accounts
  name                               = each.value.name
  location                           = coalesce(var.cosmos_location, var.location)
  resource_group_name                = azurerm_resource_group.this.name
  offer_type                         = "Standard"
  kind                               = "GlobalDocumentDB"
  local_authentication_enabled       = false
  public_network_access_enabled      = false
  access_key_metadata_writes_enabled = false
  automatic_failover_enabled         = each.value.failover != null
  minimal_tls_version                = "Tls12"
  tags                               = local.tags

  consistency_policy { consistency_level = "Session" }

  geo_location {
    location          = coalesce(var.cosmos_location, var.location)
    failover_priority = 0
    zone_redundant    = local.prod
  }

  dynamic "geo_location" {
    for_each = each.value.failover == null ? [] : [each.value.failover]
    content {
      location          = geo_location.value
      failover_priority = 1
    }
  }

  backup {
    type = "Continuous"
    tier = "Continuous30Days"
  }
}

resource "azurerm_cosmosdb_sql_database" "app" {
  name                = "quoting"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this["app"].name
}

resource "azurerm_cosmosdb_sql_container" "app" {
  for_each              = var.cosmos_containers
  name                  = each.key
  resource_group_name   = azurerm_resource_group.this.name
  account_name          = azurerm_cosmosdb_account.this["app"].name
  database_name         = azurerm_cosmosdb_sql_database.app.name
  partition_key_paths   = ["/session_id"]
  partition_key_version = 2
  default_ttl           = each.value.default_ttl

  autoscale_settings { max_throughput = each.value.max_throughput }

  # events: the payload is never queried, so it is not indexed (saves RU/s).
  dynamic "indexing_policy" {
    for_each = each.key == "events" ? [1] : []
    content {
      indexing_mode = "consistent"
      included_path { path = "/*" }
      excluded_path { path = "/data/*" }
    }
  }
}

resource "azurerm_storage_account" "this" {
  for_each                        = local.storage_accounts
  name                            = local.name.storage[each.key]
  location                        = var.location
  resource_group_name             = azurerm_resource_group.this.name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  public_network_access           = local.ops && each.key == "kb" ? "Enabled" : "Disabled" # dev: upload kb-docs from the deployer's IP
  tags                            = local.tags

  dynamic "network_rules" {
    for_each = local.ops && each.key == "kb" ? [1] : []
    content {
      default_action = "Deny"
      ip_rules       = [for ip in var.operator_ips : trimsuffix(ip, "/32")]
      bypass         = ["AzureServices"]
    }
  }

  blob_properties {
    delete_retention_policy { days = 7 }
    container_delete_retention_policy { days = 7 }
  }
}

resource "azurerm_storage_container" "this" {
  for_each           = local.storage_containers
  name               = each.value.name
  storage_account_id = azurerm_storage_account.this[each.value.account].id
}

# uploads: customer documents are deleted after 30 days and scanned for malware on upload (Defender for Storage).
# Scan results are written as blob index tags ("Malware Scanning scan result"). No Event Grid topic is provisioned.
resource "azurerm_storage_management_policy" "uploads" {
  storage_account_id = azurerm_storage_account.this["uploads"].id

  rule {
    name    = "delete-after-30-days"
    enabled = true
    filters { blob_types = ["blockBlob"] }
    actions {
      base_blob { delete_after_days_since_creation_greater_than = 30 }
    }
  }
}

resource "azurerm_security_center_storage_defender" "uploads" {
  storage_account_id                          = azurerm_storage_account.this["uploads"].id
  malware_scanning_on_upload_enabled          = true
  malware_scanning_on_upload_cap_gb_per_month = 100
  sensitive_data_discovery_enabled            = false
  override_subscription_settings_enabled      = true
}
