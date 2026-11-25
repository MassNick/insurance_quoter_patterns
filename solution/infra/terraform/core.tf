resource "azurerm_resource_group" "this" {
  name     = "rg-${local.suffix}"
  location = var.location
  tags     = local.tags
}

# ---- Observability: Log Analytics + workspace-based Application Insights (architecture 13) ----
resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.suffix}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = local.prod ? 90 : 30
  tags                = local.tags
}

resource "azurerm_application_insights" "this" {
  name                = "appi-${local.suffix}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = local.tags
}

# ---- One user-assigned identity per container app ----
resource "azurerm_user_assigned_identity" "app" {
  for_each            = toset(local.app_keys)
  name                = "id-${each.key}-${local.suffix}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

# ---- Key Vault: RBAC mode, no public access. Holds the session_ref signing key only ----
resource "azurerm_key_vault" "this" {
  name                          = local.name.kv
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  public_network_access_enabled = false
  purge_protection_enabled      = local.prod
  soft_delete_retention_days    = local.prod ? 90 : 7
  tags                          = local.tags

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
}

# SESSION_REF_KEY (shared by the BFF and mcp-quoting) and SESSION_COOKIE_KEY: generated here, written through ARM (no data-plane
# path needed, so a hosted runner can apply), never output. Rotation: taint the random_password and apply, then restart the apps.
resource "random_password" "kv_secret" {
  for_each = local.kv_secrets
  length   = 64
  special  = false
}

resource "azapi_resource" "kv_secret" {
  for_each  = local.kv_secrets
  type      = "Microsoft.KeyVault/vaults/secrets@2023-07-01"
  name      = each.key
  parent_id = azurerm_key_vault.this.id
  body      = { properties = { contentType = "text/plain", attributes = { enabled = true } } }

  sensitive_body = { properties = { value = random_password.kv_secret[each.key].result } }
}

# ---- Registry and search ----
resource "azurerm_container_registry" "this" {
  name                          = local.name.acr
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  sku                           = "Premium"
  admin_enabled                 = false
  public_network_access_enabled = local.ops
  data_endpoint_enabled         = true
  retention_policy_in_days      = 7 # untagged manifests
  zone_redundancy_enabled       = local.prod
  tags                          = local.tags

  dynamic "network_rule_set" {
    for_each = local.ops ? [1] : []
    content {
      default_action = "Deny"
      ip_rule        = [for ip in var.operator_ips : { action = "Allow", ip_range = ip }]
    }
  }
}

resource "azurerm_search_service" "this" {
  name                          = local.name.search
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  sku                           = var.search_sku
  replica_count                 = var.search_replicas
  semantic_search_sku           = "standard"
  local_authentication_enabled  = false
  public_network_access_enabled = local.ops
  allowed_ips                   = var.operator_ips
  tags                          = local.tags

  identity { type = "SystemAssigned" }
}

# Private ingestion of the knowledge source: Search reaches kb-docs and the embedding model over shared private links.
# Approve the pending connections on the targets (deploy/post_deploy.sh does it).
resource "azurerm_search_shared_private_link_service" "this" {
  for_each           = { kb_storage = { target = azurerm_storage_account.this["kb"].id, group = "blob" }, foundry = { target = module.foundry.account_id, group = "openai_account" } }
  name               = "spl-${each.key}"
  search_service_id  = azurerm_search_service.this.id
  target_resource_id = each.value.target
  subresource_name   = each.value.group
  request_message    = "Foundry IQ knowledge source ingestion"
}
