# Container Apps: one internal, VNet-integrated environment and ONE app definition rendered for bff, mcp-quoting, mcp-a2ui and web.
# The apps are defined in locals.tf (app_defs). CD rolls new revisions (az containerapp update), so the image and traffic weights
# are ignored here: Terraform owns the shape, the pipeline owns the release.

resource "azurerm_container_app_environment" "this" {
  name                           = "cae-${local.suffix}"
  location                       = var.location
  resource_group_name            = azurerm_resource_group.this.name
  logs_destination               = "log-analytics" # azurerm 5: required with log_analytics_workspace_id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id       = module.network.subnet_ids["aca-infra"]
  internal_load_balancer_enabled = true
  public_network_access          = "Disabled" # required for the Front Door private link
  zone_redundancy_enabled        = local.prod
  tags                           = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# Apps resolve each other (and the Foundry toolbox reaches the MCP servers) through the environment's private IP.
resource "azurerm_private_dns_zone" "aca" {
  name                = azurerm_container_app_environment.this.default_domain
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_a_record" "aca" {
  name                = "*"
  private_dns_zone_id = azurerm_private_dns_zone.aca.id
  ttl                 = 300
  records             = [azurerm_container_app_environment.this.static_ip_address]
}

resource "azurerm_private_dns_zone_virtual_network_link" "aca" {
  name                = "link-aca"
  private_dns_zone_id = azurerm_private_dns_zone.aca.id
  virtual_network_id  = module.network.vnet_id
}

resource "azurerm_container_app" "this" {
  for_each                     = local.apps
  name                         = local.app_name[each.key]
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  workload_profile_name        = "Consumption"
  revision_mode                = "Multiple" # CD shifts traffic between revisions (canary, rollback)
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app[each.key].id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app[each.key].id
  }

  # Key Vault references, resolved by the platform with the app's identity.
  dynamic "secret" {
    for_each = each.value.secrets
    content {
      name                = lower(replace(secret.key, "_", "-"))
      identity            = azurerm_user_assigned_identity.app[each.key].id
      key_vault_secret_id = secret.value
    }
  }

  ingress {
    external_enabled         = true # "external" inside an internal environment = reachable from the VNet and the private link, not the internet
    target_port              = each.value.port
    transport                = "auto"
    session_affinity_enabled = each.value.affinity

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = each.value.min
    max_replicas = each.value.max

    http_scale_rule {
      name                = "http"
      concurrent_requests = tostring(each.value.concurrency)
    }

    container {
      name   = each.key
      image  = var.app_images[each.key]
      cpu    = each.value.cpu
      memory = each.value.memory

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app[each.key].client_id
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = nonsensitive(azurerm_application_insights.this.connection_string) # a connection string identifies the target, it grants no read access
      }
      dynamic "env" {
        for_each = each.value.env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.value.secrets
        content {
          name        = env.key
          secret_name = lower(replace(env.key, "_", "-"))
        }
      }

      liveness_probe {
        transport = "HTTP"
        port      = each.value.port
        path      = each.value.health
      }
      readiness_probe {
        transport = "HTTP"
        port      = each.value.port
        path      = each.value.health
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image, ingress[0].traffic_weight]
    postcondition {
      condition     = self.ingress[0].fqdn == "${self.name}.${azurerm_container_app_environment.this.default_domain}"
      error_message = "The app FQDN is not <app>.<environment domain>. The sibling URLs in locals.tf (fqdn) assume that shape: fix them."
    }
  }

  depends_on = [azurerm_role_assignment.this, azurerm_private_endpoint.this]
}
