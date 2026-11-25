# Remote state. Local state is used until you enable this block.
#
# One-time setup (an admin creates the state storage once per subscription; nothing here creates it):
#   az group create -n rg-aiq-tfstate -l canadacentral
#   az storage account create -n <stateaccount> -g rg-aiq-tfstate --sku Standard_ZRS --allow-shared-key-access false
#   az storage container create -n tfstate --account-name <stateaccount> --auth-mode login
#   grant the Terraform identity "Storage Blob Data Contributor" on that account
#
# Then uncomment, and run:  terraform init -backend-config="key=aiq-<env>.tfstate"
# (one state key per environment). Never commit state, plan files or *.tfvars.
#
# terraform {
#   backend "azurerm" {
#     resource_group_name  = "rg-aiq-tfstate"
#     storage_account_name = "<stateaccount>"
#     container_name       = "tfstate"
#     use_azuread_auth     = true
#     use_oidc             = true # GitHub Actions: ARM_USE_OIDC=true, ARM_CLIENT_ID, ARM_TENANT_ID
#   }
# }
