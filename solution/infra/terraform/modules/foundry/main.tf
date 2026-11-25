# Foundry account (AIServices, project management on) + project + model deployments + guardrails + connections
# + standard agent setup (BYO Cosmos DB, Storage, AI Search) with the hosted agent's VNet injection (agent-egress).
# azurerm covers the account, project and RBAC; azapi covers what azurerm lacks: guardrails with egress rules (2026-09-01),
# deployments (serialised: ARM rejects parallel writes on one account), connections and capability hosts.
# Hosted-agent network injection must be set when the account is created (it cannot be added later).

terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm" }
    azapi   = { source = "Azure/azapi" } # not the hashicorp/azapi default: modules need the source spelled out
  }
}

variable "name" {
  description = "Account name, also its custom sub-domain."
  type        = string
}
variable "project_name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "tags" { type = map(string) }
variable "agent_subnet_id" { type = string }
variable "operator_ips" {
  type    = list(string)
  default = [] # dev only: the deployer's IPs reach the account (firewall rule, default deny)
}
variable "model_deployments" {
  type = map(object({
    model_name    = string
    model_version = string
    model_format  = string
    sku_name      = string
    capacity      = number
  }))
}
variable "deployment_guardrails" {
  description = "Deployment name -> guardrail name. Deployments not listed (embeddings) get none."
  type        = map(string)
}
variable "app_insights_id" { type = string }
variable "app_insights_connection_string" {
  type      = string
  sensitive = true
}
variable "byo" {
  description = "Resources that hold the Agent Service's own state (separate from the app's Cosmos account)."
  type = object({
    cosmos  = object({ id = string, name = string, endpoint = string })
    storage = object({ id = string, endpoint = string })
    search  = object({ id = string, endpoint = string })
  })
}
variable "egress_hosts" { type = list(string) }
variable "egress_mode" { type = string }
variable "role_ids" { type = map(string) }

locals {
  arm = "2025-06-01" # capability hosts and connections as documented; deployments reuse it (the docs example uses 2023-05-01). [UNVERIFIED]
  rai = "2026-09-01" # guardrails with egressPolicy (stable per the hosted-agent guardrails doc)

  # Guardrail content filters. Prompt/Completion are the model intervention points; PreRun/PostRun/PostToolCall are the agent ones (preview).
  harm = ["Hate", "Sexual", "Violence", "Selfharm"]
  # Every harm category on input and output, Medium, blocking; plus prompt shields, protected material and XPIA.
  harm_filters = [for p in setproduct(local.harm, ["Prompt", "PreRun", "Completion", "PostRun"]) : { name = p[0], source = p[1], severityThreshold = "Medium", blocking = true, enabled = true }]
  shield_filters = concat(
    [for s in ["Prompt", "PreRun"] : { name = "Jailbreak", source = s, blocking = true, enabled = true }],
    [for s in ["Completion", "PostRun"] : { name = "Protected Material Text", source = s, blocking = true, enabled = true }],
    [{ name = "Indirect Attack", source = "PostToolCall", blocking = true, enabled = true }], # XPIA on MCP and knowledge results. [UNVERIFIED] filter name
  )
  filters = {
    # Chat models: Sexual on the customer's input only annotates (blocking = false). The filter rates normal Quebec wording
    # ("Je veux une soumission pour mon auto": soumission = a quote) as sexual, severity high, and refuses it at every
    # threshold. Decided by the product owner on the first dev deploy (2026-11-25); the replies stay blocked on Sexual.
    quoting = concat(
      [for f in local.harm_filters : merge(f, { blocking = !(f.name == "Sexual" && contains(["Prompt", "PreRun"], f.source)) })],
      local.shield_filters,
    )
    # Document model: everything blocking, plus XPIA on the prompt (the uploaded image is untrusted input).
    document = concat(local.harm_filters, local.shield_filters, [{ name = "Indirect Attack", source = "Prompt", blocking = true, enabled = true }])
  }
  guardrails = {
    quoting-guardrail  = { filters = local.filters.quoting, egress = true }
    document-guardrail = { filters = local.filters.document, egress = false }
  }
  egress_rules = [for h in var.egress_hosts : {
    name     = "allow-${replace(replace(h, "*", "any"), ".", "-")}"
    ruleType = "Fqdn"
    match    = { host = h }
    action   = { actionType = "Allow" }
  }]

  # Connections the capability host and tracing need. Project scope unless noted.
  connections = {
    byo-cosmos  = { parent = azurerm_cognitive_account_project.this.id, type = "projects/connections", category = "CosmosDb", target = var.byo.cosmos.endpoint, resource_id = var.byo.cosmos.id, secret = null }
    byo-storage = { parent = azurerm_cognitive_account_project.this.id, type = "projects/connections", category = "AzureStorageAccount", target = var.byo.storage.endpoint, resource_id = var.byo.storage.id, secret = null }
    byo-search  = { parent = azurerm_cognitive_account_project.this.id, type = "projects/connections", category = "CognitiveSearch", target = var.byo.search.endpoint, resource_id = var.byo.search.id, secret = null }
    appinsights = { parent = azurerm_cognitive_account.this.id, type = "connections", category = "AppInsights", target = var.app_insights_id, resource_id = var.app_insights_id, secret = var.app_insights_connection_string }
  }

  # Standard setup prerequisites for the project identity. The BYO accounts are dedicated to the Agent Service, so account scope is used.
  project_roles = {
    cosmos_operator     = { role = "cosmos_db_operator", scope = var.byo.cosmos.id }
    storage_contributor = { role = "storage_account_contributor", scope = var.byo.storage.id }
    storage_blob_owner  = { role = "storage_blob_data_owner", scope = var.byo.storage.id }
    search_index        = { role = "search_index_data_contributor", scope = var.byo.search.id }
    search_service      = { role = "search_service_contributor", scope = var.byo.search.id }
    foundry_user        = { role = "foundry_user", scope = azurerm_cognitive_account.this.id }
  }
}

resource "azurerm_cognitive_account" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = var.name
  project_management_enabled    = true
  local_auth_enabled            = false
  public_network_access_enabled = length(var.operator_ips) > 0
  tags                          = var.tags

  identity { type = "SystemAssigned" }

  network_acls {
    default_action = "Deny"
    ip_rules       = [for ip in var.operator_ips : trimsuffix(ip, "/32")] # the account rejects /32
    bypass         = "AzureServices"
  }

  network_injection {
    scenario  = "agent"
    subnet_id = var.agent_subnet_id
  }
}

resource "azurerm_cognitive_account_project" "this" {
  name                 = var.project_name
  cognitive_account_id = azurerm_cognitive_account.this.id
  location             = var.location
  display_name         = var.project_name
  tags                 = var.tags

  identity { type = "SystemAssigned" }
}

resource "azapi_resource" "guardrail" {
  for_each                  = local.guardrails
  type                      = "Microsoft.CognitiveServices/accounts/raiPolicies@${local.rai}"
  name                      = each.key
  parent_id                 = azurerm_cognitive_account.this.id
  schema_validation_enabled = false # the agent intervention points (PreRun, PostRun, PostToolCall) are preview
  locks                     = [azurerm_cognitive_account.this.id]

  body = {
    properties = merge(
      { mode = "Blocking", basePolicyName = "Microsoft.DefaultV2", contentFilters = each.value.filters },
      each.value.egress ? { egressPolicy = { mode = var.egress_mode, defaultAction = "Deny", rules = local.egress_rules } } : {},
    )
  }
}

resource "azapi_resource" "deployment" {
  for_each  = var.model_deployments
  type      = "Microsoft.CognitiveServices/accounts/deployments@${local.arm}"
  name      = each.key
  parent_id = azurerm_cognitive_account.this.id
  locks     = [azurerm_cognitive_account.this.id]

  body = {
    sku = { name = each.value.sku_name, capacity = each.value.capacity }
    properties = merge(
      { model = { format = each.value.model_format, name = each.value.model_name, version = each.value.model_version }, versionUpgradeOption = "OnceNewDefaultVersionAvailable" },
      contains(keys(var.deployment_guardrails), each.key) ? { raiPolicyName = azapi_resource.guardrail[var.deployment_guardrails[each.key]].name } : {},
    )
  }
}

resource "azapi_resource" "connection" {
  for_each                  = local.connections
  type                      = "Microsoft.CognitiveServices/accounts/${each.value.type}@${local.arm}"
  name                      = each.key
  parent_id                 = each.value.parent
  schema_validation_enabled = false

  body = {
    properties = {
      category      = each.value.category
      target        = each.value.target
      authType      = each.value.secret == null ? "AAD" : "ApiKey"
      isSharedToAll = each.value.secret != null
      metadata      = { ApiType = "Azure", ResourceId = each.value.resource_id, location = var.location }
    }
  }
  sensitive_body = each.value.secret == null ? null : { properties = { credentials = { key = each.value.secret } } }
}

resource "azurerm_role_assignment" "project" {
  for_each           = local.project_roles
  scope              = each.value.scope
  role_definition_id = var.role_ids[each.value.role]
  principal_id       = azurerm_cognitive_account_project.this.identity[0].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_cosmosdb_sql_role_assignment" "project" {
  resource_group_name = var.resource_group_name
  account_name        = var.byo.cosmos.name
  role_definition_id  = "${var.byo.cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002" # Cosmos DB Built-in Data Contributor
  principal_id        = azurerm_cognitive_account_project.this.identity[0].principal_id
  scope               = var.byo.cosmos.id
}

# Capability hosts: the account one is created by Azure with the network injection (<account>@aml_aiagentservice, on the
# agent subnet); declaring it again fails with 409. The project one names the three BYO connections. Immutable once created.
resource "azapi_resource" "capability_host_project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/capabilityHosts@${local.arm}"
  name                      = "agents-project"
  parent_id                 = azurerm_cognitive_account_project.this.id
  schema_validation_enabled = false

  body = {
    properties = {
      capabilityHostKind       = "Agents"
      threadStorageConnections = [azapi_resource.connection["byo-cosmos"].name]
      storageConnections       = [azapi_resource.connection["byo-storage"].name]
      vectorStoreConnections   = [azapi_resource.connection["byo-search"].name]
    }
  }

  depends_on = [
    azurerm_role_assignment.project,
    azurerm_cosmosdb_sql_role_assignment.project,
  ]
}

output "account_id" { value = azurerm_cognitive_account.this.id }
output "account_principal_id" { value = azurerm_cognitive_account.this.identity[0].principal_id }
output "project_id" { value = azurerm_cognitive_account_project.this.id }
output "project_principal_id" { value = azurerm_cognitive_account_project.this.identity[0].principal_id }
output "project_endpoint" { value = "https://${var.name}.services.ai.azure.com/api/projects/${var.project_name}" }
output "guardrail_ids" { value = { for k, g in azapi_resource.guardrail : k => g.id } }
