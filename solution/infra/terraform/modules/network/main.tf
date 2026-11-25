# VNet, the three subnets of architecture section 2 (#22), the private DNS zones and an optional NAT gateway.

variable "name_suffix" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "vnet_cidr" { type = string }
variable "enable_nat_gateway" { type = bool }
variable "tags" { type = map(string) }

locals {
  # aca-infra and agent-egress are delegated to Microsoft.App/environments (Container Apps and the hosted-agent runtime).
  subnets = {
    aca-infra         = { cidr = cidrsubnet(var.vnet_cidr, 7, 0), delegated = true }
    private-endpoints = { cidr = cidrsubnet(var.vnet_cidr, 8, 2), delegated = false }
    agent-egress      = { cidr = cidrsubnet(var.vnet_cidr, 8, 3), delegated = true }
  }

  # Zones for every private-link target: Foundry (3 zones), Cosmos, Blob, Search, Key Vault, ACR.
  zones = {
    cosmos            = "privatelink.documents.azure.com"
    blob              = "privatelink.blob.core.windows.net"
    search            = "privatelink.search.windows.net"
    vault             = "privatelink.vaultcore.azure.net"
    acr               = "privatelink.azurecr.io"
    cognitiveservices = "privatelink.cognitiveservices.azure.com"
    openai            = "privatelink.openai.azure.com"
    aiservices        = "privatelink.services.ai.azure.com"
  }
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${var.name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_cidr]
  tags                = var.tags
}

resource "azurerm_subnet" "this" {
  for_each             = local.subnets
  name                 = each.key
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [each.value.cidr]

  dynamic "delegation" {
    for_each = each.value.delegated ? [1] : []
    content {
      name = "Microsoft.App.environments"
      service_delegation {
        name    = "Microsoft.App/environments"
        actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
      }
    }
  }
}

# Azure's default rules only: the hosted-agent and Container Apps subnets need unrestricted outbound (Entra, MCR, Azure Monitor).
resource "azurerm_network_security_group" "this" {
  name                = "nsg-${var.name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_subnet_network_security_group_association" "this" {
  for_each                  = local.subnets
  subnet_id                 = azurerm_subnet.this[each.key].id
  network_security_group_id = azurerm_network_security_group.this.id
}

resource "azurerm_private_dns_zone" "this" {
  for_each            = local.zones
  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each            = local.zones
  name                = "link-${each.key}"
  private_dns_zone_id = azurerm_private_dns_zone.this[each.key].id
  virtual_network_id  = azurerm_virtual_network.this.id
  tags                = var.tags
}

resource "azurerm_public_ip" "nat" {
  count               = var.enable_nat_gateway ? 1 : 0
  name                = "pip-nat-${var.name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_nat_gateway" "this" {
  count               = var.enable_nat_gateway ? 1 : 0
  name                = "ng-${var.name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "Standard"
  tags                = var.tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  count                = var.enable_nat_gateway ? 1 : 0
  nat_gateway_id       = azurerm_nat_gateway.this[0].id
  public_ip_address_id = azurerm_public_ip.nat[0].id
}

resource "azurerm_subnet_nat_gateway_association" "this" {
  count          = var.enable_nat_gateway ? 1 : 0
  subnet_id      = azurerm_subnet.this["aca-infra"].id
  nat_gateway_id = azurerm_nat_gateway.this[0].id
}

output "vnet_id" { value = azurerm_virtual_network.this.id }
output "subnet_ids" { value = { for k, s in azurerm_subnet.this : k => s.id } }
output "dns_zone_ids" { value = { for k, z in azurerm_private_dns_zone.this : k => z.id } }
output "nat_public_ip" { value = one(azurerm_public_ip.nat[*].ip_address) }
