# Endpoints, names and identity ids only. No keys, no secrets, no connection strings.
# deploy/post_deploy.sh reads this with `terraform output -json`.

output "resource_group" { value = azurerm_resource_group.this.name }
output "tenant_id" { value = data.azurerm_client_config.current.tenant_id }

# Foundry
output "foundry_project_endpoint" { value = module.foundry.project_endpoint }
output "foundry_project_id" { value = module.foundry.project_id }
output "foundry_account_id" { value = module.foundry.account_id }
output "model_deployments" { value = { for role, d in local.model_roles : role => { deployment = d, model = local.model_deployments[d].model_name } } }
output "guardrail_ids" { value = module.foundry.guardrail_ids }
output "toolbox_mcp_url" { value = "${module.foundry.project_endpoint}/toolboxes/auto-quoting-toolbox/mcp?api-version=v1" }
output "kb_mcp_url" { value = "https://${local.name.search}.search.windows.net/knowledgebases/auto-quoting-kb/mcp?api-version=2026-11-01-preview" }

# Data
output "search_endpoint" { value = "https://${local.name.search}.search.windows.net" }
output "kb_storage_id" { value = azurerm_storage_account.this["kb"].id }
output "uploads_account_url" { value = azurerm_storage_account.this["uploads"].primary_blob_endpoint }
output "cosmos_endpoint" { value = azurerm_cosmosdb_account.this["app"].endpoint }
output "cosmos_database" { value = azurerm_cosmosdb_sql_database.app.name }
output "key_vault_uri" { value = azurerm_key_vault.this.vault_uri }

# Registry, apps, edge
output "acr_name" { value = azurerm_container_registry.this.name }
output "acr_login_server" { value = azurerm_container_registry.this.login_server }
output "container_app_environment_id" { value = azurerm_container_app_environment.this.id }
output "app_names" { value = local.app_name }
output "app_fqdns" { value = local.fqdn }
output "frontdoor_hostname" { value = module.frontdoor.endpoint_host }
output "nat_public_ip" { value = module.network.nat_public_ip }

# Identities
output "identity_client_ids" { value = { for k, i in azurerm_user_assigned_identity.app : k => i.client_id } }
output "bff_principal_id" { value = local.bff }
output "project_principal_id" { value = module.foundry.project_principal_id }
# The agent identity as last applied. The terraform action reads it back from the state on every pass, so a pass that is not given the id keeps the agent's roles.
output "quote_agent_principal_id" { value = var.quote_agent_principal_id }

# Private endpoint connections that someone must approve once: Search shared private links (kb storage, Foundry) and Front Door (Container Apps).
output "private_link_approval_targets" {
  value = [azurerm_storage_account.this["kb"].id, module.foundry.account_id, azurerm_container_app_environment.this.id]
}
