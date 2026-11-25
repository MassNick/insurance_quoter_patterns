module "network" {
  source              = "./modules/network"
  name_suffix         = local.suffix
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  vnet_cidr           = var.vnet_cidr
  enable_nat_gateway  = var.enable_nat_gateway
  tags                = local.tags
}

module "foundry" {
  source              = "./modules/foundry"
  name                = local.name.foundry
  project_name        = "auto-quoting"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
  agent_subnet_id     = module.network.subnet_ids["agent-egress"]
  model_deployments   = local.model_deployments
  operator_ips        = var.operator_ips

  # chat models get the quoting guardrail, the document model its own; the embedding model gets none
  deployment_guardrails = {
    (local.model_roles.main)     = "quoting-guardrail"
    (local.model_roles.memory)   = "quoting-guardrail"
    (local.model_roles.document) = "document-guardrail"
  }

  app_insights_id                = azurerm_application_insights.this.id
  app_insights_connection_string = azurerm_application_insights.this.connection_string
  egress_hosts                   = local.egress_hosts
  egress_mode                    = var.agent_egress_mode
  role_ids                       = local.role_ids

  byo = {
    cosmos  = { id = azurerm_cosmosdb_account.this["foundry"].id, name = azurerm_cosmosdb_account.this["foundry"].name, endpoint = azurerm_cosmosdb_account.this["foundry"].endpoint }
    storage = { id = azurerm_storage_account.this["foundry"].id, endpoint = azurerm_storage_account.this["foundry"].primary_blob_endpoint }
    search  = { id = azurerm_search_service.this.id, endpoint = "https://${local.name.search}.search.windows.net" }
  }
}

# RemoteTool project connections behind the toolbox entries: Entra auth with the project identity, audience = the MCP app registration.
# [UNVERIFIED] authType ProjectManagedIdentity is absent from the published ARM schema (2026-09-15-preview) although
# `azd ai connection create --kind remote-tool --auth-type project-managed-identity --audience ...` exists. See README.
locals {
  remote_tools = merge(
    { for k in ["mcp-quoting", "mcp-a2ui"] : "conn-${k}" => { target = "https://${local.fqdn[k]}/mcp", audience = local.mcp_audience[k] } if var.deploy_apps },
    { conn-foundry-iq = { target = "https://${local.name.search}.search.windows.net/knowledgebases/auto-quoting-kb/mcp?api-version=2026-11-01-preview", audience = "https://search.azure.com/" } },
  )
}

resource "azapi_resource" "remote_tool" {
  for_each                  = local.remote_tools
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = each.key
  parent_id                 = module.foundry.project_id
  schema_validation_enabled = false

  body = {
    properties = {
      category      = "RemoteTool"
      authType      = "ProjectManagedIdentity"
      target        = each.value.target
      audience      = each.value.audience
      isSharedToAll = true
    }
  }
}

module "frontdoor" {
  source                 = "./modules/frontdoor"
  name_suffix            = local.suffix
  location               = var.location
  resource_group_name    = azurerm_resource_group.this.name
  tags                   = local.tags
  private_link_target_id = azurerm_container_app_environment.this.id
  origins = {
    for k, r in local.fd_routes : k => { host = local.fqdn[k], patterns = r.patterns, health = local.app_defs[k].health, cache = r.cache }
    if contains(keys(local.apps), k)
  }
}
