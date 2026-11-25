# Front Door Premium + WAF with one Private Link origin per app of the Container Apps environment (architecture section 12).
# SSE: no caching and no compression on the API route, and the longest origin timeout (V12 is still to be verified).

variable "name_suffix" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "tags" { type = map(string) }
variable "private_link_target_id" {
  description = "Container Apps environment resource id (private link sub-resource managedEnvironments)."
  type        = string
}
variable "origins" {
  description = "App key -> origin host, route patterns, health probe path and whether the route caches (static assets)."
  type = map(object({
    host     = string
    patterns = list(string)
    health   = string
    cache    = bool
  }))
}
resource "azurerm_cdn_frontdoor_profile" "this" {
  name                     = "afd-${var.name_suffix}"
  resource_group_name      = var.resource_group_name
  sku_name                 = "Premium_AzureFrontDoor"
  response_timeout_seconds = 240
  tags                     = var.tags
}

resource "azurerm_cdn_frontdoor_endpoint" "this" {
  name                     = "ep-${var.name_suffix}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id
  tags                     = var.tags
}

resource "azurerm_cdn_frontdoor_origin_group" "this" {
  for_each                 = var.origins
  name                     = "og-${each.key}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id

  load_balancing {}

  health_probe {
    path                = each.value.health
    protocol            = "Https"
    request_type        = "GET"
    interval_in_seconds = 60
  }
}

resource "azurerm_cdn_frontdoor_origin" "this" {
  for_each                       = var.origins
  name                           = "o-${each.key}"
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.this[each.key].id
  host_name                      = each.value.host
  origin_host_header             = each.value.host
  certificate_name_check_enabled = true

  private_link {
    location               = var.location
    private_link_target_id = var.private_link_target_id
    target_type            = "managedEnvironments"
    request_message        = "AFD Private Link Request"
  }
}

resource "azurerm_cdn_frontdoor_route" "this" {
  for_each                      = var.origins
  name                          = "r-${each.key}"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.this.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.this[each.key].id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.this[each.key].id]
  patterns_to_match             = each.value.patterns
  supported_protocols           = ["Http", "Https"]
  forwarding_protocol           = "HttpsOnly"
  https_redirect_enabled        = true
  link_to_default_domain        = true

  dynamic "cache" {
    for_each = each.value.cache ? [1] : []
    content {
      query_string_caching_behavior = "IgnoreQueryString"
      compression_enabled           = true
      content_types_to_compress     = ["text/html", "text/css", "application/javascript", "application/json", "image/svg+xml"]
    }
  }
}

resource "azurerm_cdn_frontdoor_firewall_policy" "this" {
  name                = "waf${replace(var.name_suffix, "-", "")}"
  resource_group_name = var.resource_group_name
  sku_name            = azurerm_cdn_frontdoor_profile.this.sku_name
  mode                = "Prevention"
  tags                = var.tags

  managed_rule {
    type    = "Microsoft_DefaultRuleSet"
    version = "2.1"
    action  = "Block"
  }

  managed_rule {
    type    = "Microsoft_BotManagerRuleSet"
    version = "1.1"
    action  = "Block"
  }

  custom_rule {
    name                           = "RateLimitApi"
    type                           = "RateLimitRule"
    priority                       = 100
    action                         = "Block"
    rate_limit_duration_in_minutes = 1
    rate_limit_threshold           = 60 # requests per minute per client IP on /api/

    match_condition {
      match_variable = "RequestUri"
      operator       = "Contains"
      match_values   = ["/api/"]
      transforms     = ["Lowercase"]
    }
  }
}

resource "azurerm_cdn_frontdoor_security_policy" "this" {
  name                     = "sp-${var.name_suffix}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id

  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.this.id

      association {
        patterns_to_match = ["/*"]
        domain {
          cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.this.id
        }
      }
    }
  }
}

output "endpoint_host" { value = azurerm_cdn_frontdoor_endpoint.this.host_name }
output "profile_id" { value = azurerm_cdn_frontdoor_profile.this.id }
