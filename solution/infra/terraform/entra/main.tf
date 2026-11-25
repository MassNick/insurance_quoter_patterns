# The two MCP app registrations and the app-role grants of architecture section 11. Separate stack on purpose: it needs Microsoft Graph
# permissions (Application.ReadWrite.OwnedBy, AppRoleAssignment.ReadWrite.All) that the platform pipeline should not hold.
# Apply once per environment, after the main stack (it needs the two principal ids) and before the Foundry data-plane step:
#   terraform -chdir=.. output -raw bff_principal_id / project_principal_id  ->  -var bff_principal_id=... -var project_principal_id=...

variable "workload" {
  type    = string
  default = "aiq"
}
variable "environment" { type = string }
variable "bff_principal_id" { type = string }
variable "project_principal_id" { type = string }

locals {
  # app -> app roles it defines. The audience (api://<workload>-<env>-<app>) is the same string the main stack injects.
  apps = {
    mcp-quoting = ["Tools.Invoke", "Lookup.Read"]
    mcp-a2ui    = ["Tools.Invoke"]
  }
  roles = { for p in flatten([for a, rs in local.apps : [for r in rs : { key = "${a}/${r}", app = a, value = r }]]) : p.key => p }

  # Toolbox calls arrive as the Foundry project identity; the BFF calls lookups on mcp-quoting and renders on mcp-a2ui itself.
  grants = {
    quoting-project-tools = { app = "mcp-quoting", role = "Tools.Invoke", principal = var.project_principal_id }
    quoting-bff-lookup    = { app = "mcp-quoting", role = "Lookup.Read", principal = var.bff_principal_id }
    a2ui-project-tools    = { app = "mcp-a2ui", role = "Tools.Invoke", principal = var.project_principal_id }
    a2ui-bff-tools        = { app = "mcp-a2ui", role = "Tools.Invoke", principal = var.bff_principal_id }
  }
}

data "azuread_client_config" "current" {}

resource "random_uuid" "role" {
  for_each = local.roles
}

resource "azuread_application" "mcp" {
  for_each         = local.apps
  display_name     = "${var.workload}-${var.environment}-${each.key}"
  identifier_uris  = ["api://${data.azuread_client_config.current.tenant_id}/${var.workload}-${var.environment}-${each.key}"] # tenant policy: the URI names the tenant
  owners           = [data.azuread_client_config.current.object_id]                                                           # without owners a non-admin can no longer change or delete the app
  sign_in_audience = "AzureADMyOrg"

  # v2 tokens: aud is the client id (pass the client_ids output as mcp_client_ids), and tenants whose default app policy
  # only accepts identifier URIs with a verified domain, tenant id or app id still accept this api:// URI.
  api {
    requested_access_token_version = 2
  }

  dynamic "app_role" {
    for_each = each.value
    content {
      id                   = random_uuid.role["${each.key}/${app_role.value}"].result
      value                = app_role.value
      display_name         = app_role.value
      description          = "Allows an application identity to call ${each.key} (${app_role.value})."
      allowed_member_types = ["Application"]
      enabled              = true
    }
  }
}

resource "azuread_service_principal" "mcp" {
  for_each                     = local.apps
  client_id                    = azuread_application.mcp[each.key].client_id
  owners                       = [data.azuread_client_config.current.object_id]
  app_role_assignment_required = true # only identities holding an app role get a token
}

resource "azuread_app_role_assignment" "this" {
  for_each            = local.grants
  app_role_id         = random_uuid.role["${each.value.app}/${each.value.role}"].result
  principal_object_id = each.value.principal
  resource_object_id  = azuread_service_principal.mcp[each.value.app].object_id
}

output "audiences" {
  value = { for k, a in azuread_application.mcp : k => one(a.identifier_uris) }
}

# For var.mcp_client_ids of the platform stack: v1 tokens carry the client id as aud, so ENTRA_AUDIENCE holds it too.
output "client_ids" {
  value = { for k, a in azuread_application.mcp : k => a.client_id }
}
