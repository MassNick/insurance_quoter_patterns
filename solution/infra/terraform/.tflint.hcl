config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "azurerm" {
  enabled = true
  version = "0.33.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

# prevent_destroy cannot depend on a variable, and dev must be disposable. Data is protected by soft delete, purge protection
# and 30-day continuous backup instead; add management locks on the prod resource group.
rule "azurerm_resources_missing_prevent_destroy" {
  enabled = false
}

# Child modules are local to this stack and inherit the provider pins and Terraform version of the root.
rule "terraform_required_version" {
  enabled = false
}

rule "terraform_required_providers" {
  enabled = false
}
