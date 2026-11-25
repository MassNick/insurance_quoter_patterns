terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.10"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}
