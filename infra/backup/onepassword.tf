variable "onepassword_vault_id" {
  type        = string
  default     = ""
  description = "Privately verified Homelab vault UUID; only use an explicitly authorized local provisioning writer."
  validation {
    condition     = !var.provision_runtime_credentials || can(regex("^[a-z0-9]{26}$", var.onepassword_vault_id))
    error_message = "Credential provisioning requires the verified Homelab vault UUID and an approved writer."
  }
}

provider "onepassword" {}

locals {
  runtime_destinations = var.provision_runtime_credentials ? toset(concat(["b2"], var.provision_ovh ? ["ovh"] : [])) : toset([])
  runtime_credentials = {
    b2 = var.provision_runtime_credentials ? {
      access_key = b2_application_key.runtime[0].application_key_id
      secret_key = b2_application_key.runtime[0].application_key
    } : null
    ovh = var.provision_ovh && var.provision_runtime_credentials ? {
      access_key = ovh_cloud_project_user_s3_credential.runtime[0].access_key_id
      secret_key = ovh_cloud_project_user_s3_credential.runtime[0].secret_access_key
    } : null
  }
}

resource "onepassword_item" "runtime" {
  for_each = local.runtime_destinations
  vault    = var.onepassword_vault_id
  title    = "homelab-backup-${each.key}"
  category = "password"
  section {
    label = "credentials"
    field {
      label = "access-key-id"
      type  = "CONCEALED"
      value = local.runtime_credentials[each.key].access_key
    }
    field {
      label = "secret-access-key"
      type  = "CONCEALED"
      value = local.runtime_credentials[each.key].secret_key
    }
    field {
      label = "restic-password"
      type  = "CONCEALED"
      password_recipe {
        length  = 64
        digits  = true
        symbols = false
      }
    }
  }
  lifecycle { prevent_destroy = true }
}
