# Remote state of the Entra stack: the platform stack's state account and container, under its own key.
# Partial configuration, like the one .github/actions/terraform writes for the platform stack: the account and the
# key are supplied at init. One key per environment, never the platform stack's (aiq-<env>.tfstate):
#
#   terraform -chdir=entra init \
#     -backend-config="resource_group_name=<state resource group>" \
#     -backend-config="storage_account_name=<state storage account>" \
#     -backend-config="key=aiq-<env>-entra.tfstate"
#
# The account has shared keys off, so whoever applies this stack needs Storage Blob Data Contributor on it.
# Validation needs no backend: terraform init -backend=false.
terraform {
  backend "azurerm" {
    container_name   = "tfstate"
    use_azuread_auth = true
  }
}
