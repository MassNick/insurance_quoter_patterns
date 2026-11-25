# The RBAC matrix of architecture section 11 as data: one table, one resource per plane. No keys anywhere.
# The Foundry project identity's own roles (standard agent setup) live in modules/foundry.
# App roles (Tools.Invoke, Lookup.Read) on the MCP app registrations are Microsoft Graph objects: see entra/.
locals {
  bff           = azurerm_user_assigned_identity.app["bff"].principal_id
  quoting       = azurerm_user_assigned_identity.app["mcp-quoting"].principal_id
  uploads_scope = "${azurerm_storage_account.this["uploads"].id}/blobServices/default/containers/uploads"
  kb_scope      = "${azurerm_storage_account.this["kb"].id}/blobServices/default/containers/kb-docs"
  search_id     = azurerm_search_service.this.id

  # Azure resource plane. Key = <who>-<what>; type defaults to ServicePrincipal.
  rbac = merge(
    # every app pulls its image; the BFF reads both secrets
    { for k in local.kv_secrets : "bff-${k}" => { principal = local.bff, role = "key_vault_secrets_user", scope = azapi_resource.kv_secret[k].id } },
    { for k in local.app_keys : "${k}-acr-pull" => { principal = azurerm_user_assigned_identity.app[k].principal_id, role = "acr_pull", scope = azurerm_container_registry.this.id } },
    {
      bff-agent-consumer = { principal = local.bff, role = "foundry_agent_consumer", scope = module.foundry.project_id } # quote-agent and document-agent
      # Owner, not Contributor: the BFF polls the Defender scan-result index tag and only Storage Blob Data Owner has blobs/tags/read.
      bff-uploads             = { principal = local.bff, role = "storage_blob_data_owner", scope = local.uploads_scope }
      quoting-session-ref-key = { principal = local.quoting, role = "key_vault_secrets_user", scope = azapi_resource.kv_secret["session-ref-key"].id }
      # Search service identity: query planning and embeddings (both roles are named in the docs) and reading kb-docs
      search-foundry-user   = { principal = azurerm_search_service.this.identity[0].principal_id, role = "cognitive_services_user", scope = module.foundry.account_id }
      search-foundry-openai = { principal = azurerm_search_service.this.identity[0].principal_id, role = "cognitive_services_openai", scope = module.foundry.account_id }
      search-kb-docs        = { principal = azurerm_search_service.this.identity[0].principal_id, role = "storage_blob_data_reader", scope = local.kb_scope }
    },
    # the pipeline identity: push images, roll revisions, run the Foundry and Search data-plane scripts, publish kb-docs
    var.cicd_principal_id == null ? {} : {
      cicd-acr-push       = { principal = var.cicd_principal_id, role = "acr_push", scope = azurerm_container_registry.this.id, type = var.cicd_principal_type }
      cicd-apps           = { principal = var.cicd_principal_id, role = "container_apps_contributor", scope = azurerm_resource_group.this.id, type = var.cicd_principal_type }
      cicd-foundry        = { principal = var.cicd_principal_id, role = "foundry_project_manager", scope = module.foundry.project_id, type = var.cicd_principal_type }
      cicd-search-service = { principal = var.cicd_principal_id, role = "search_service_contributor", scope = local.search_id, type = var.cicd_principal_type }
      cicd-search-index   = { principal = var.cicd_principal_id, role = "search_index_data_contributor", scope = local.search_id, type = var.cicd_principal_type }
      cicd-kb-docs        = { principal = var.cicd_principal_id, role = "storage_blob_data_contributor", scope = local.kb_scope, type = var.cicd_principal_type }
    },
    var.operators_group_id == null ? {} : {
      operators-logs = { principal = var.operators_group_id, role = "log_analytics_reader", scope = azurerm_log_analytics_workspace.this.id, type = "Group" }
    },
    # the hosted agent's identity exists only after the first agent deploy: apply again with quote_agent_principal_id set
    var.quote_agent_principal_id == null ? {} : {
      agent-search  = { principal = var.quote_agent_principal_id, role = "search_index_data_reader", scope = local.search_id }
      agent-project = { principal = var.quote_agent_principal_id, role = "foundry_user", scope = module.foundry.project_id } # Foundry User ("Azure AI User"): inference and toolbox calls. [UNVERIFIED] that it covers the toolbox
    },
  )

  # Cosmos data plane (built-in roles). path is appended to the database: a container, or "" for the whole database.
  cosmos_roles = { reader = "00000000-0000-0000-0000-000000000001", contributor = "00000000-0000-0000-0000-000000000002" }
  cosmos_rbac = merge(
    { for c in ["sessions", "events", "state_changes", "quotes", "confirmations", "handoffs"] : "bff-${c}" => { principal = local.bff, role = "contributor", path = "/colls/${c}" } },
    { quoting-sessions = { principal = local.quoting, role = "reader", path = "/colls/sessions" } },
    # Database scope: the framework's CosmosHistoryProvider calls create_container_if_not_exists, which reads container and database
    # metadata. The design said agent_history only; narrow it to "/colls/agent_history" once spike S5 shows that is enough.
    var.quote_agent_principal_id == null ? {} : { agent-history = { principal = var.quote_agent_principal_id, role = "contributor", path = "" } },
  )
}

resource "azurerm_role_assignment" "this" {
  for_each           = local.rbac
  scope              = each.value.scope
  role_definition_id = local.role_ids[each.value.role]
  principal_id       = each.value.principal
  principal_type     = try(each.value.type, "ServicePrincipal")
}

resource "azurerm_cosmosdb_sql_role_assignment" "app" {
  for_each            = local.cosmos_rbac
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this["app"].name
  role_definition_id  = "${azurerm_cosmosdb_account.this["app"].id}/sqlRoleDefinitions/${local.cosmos_roles[each.value.role]}"
  principal_id        = each.value.principal
  scope               = "${azurerm_cosmosdb_account.this["app"].id}/dbs/${azurerm_cosmosdb_sql_database.app.name}${each.value.path}"
  depends_on          = [azurerm_cosmosdb_sql_container.app]
}
