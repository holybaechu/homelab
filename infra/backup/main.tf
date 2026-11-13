terraform {
  required_version = "= 1.13.2"
  required_providers {
    b2 = {
      source  = "Backblaze/b2"
      version = "0.14.1"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "3.4.1"
    }
    scaleway = {
      source  = "scaleway/scaleway"
      version = "2.91.0"
    }
  }
  backend "local" {
    path = "backup.tfstate"
  }
  encryption {
    key_provider "pbkdf2" "backup" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "backup" {
      keys = key_provider.pbkdf2.backup
    }
    state {
      method   = method.aes_gcm.backup
      enforced = true
    }
    plan {
      method   = method.aes_gcm.backup
      enforced = true
    }
  }
}

variable "state_passphrase" {
  type      = string
  sensitive = true
  ephemeral = true
  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "Supply the independent backup-state passphrase from 1Password (at least 16 characters), never the Talos passphrase."
  }
}

variable "provision_scaleway" {
  type        = bool
  default     = false
  description = "Enable only after account verification, approved names/region, dedicated-project isolation and bootstrap authorization. Not a teardown switch."
}

variable "provision_runtime_credentials" {
  type        = bool
  default     = false
  description = "Enable only after an authorized Windows-local SDK writer and independent recovery are verified."
}

variable "scaleway_organization_id" {
  type    = string
  default = ""
  validation {
    condition     = !var.provision_scaleway || can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.scaleway_organization_id))
    error_message = "Supply the verified Scaleway organization UUID before provisioning."
  }
}

variable "scaleway_project_name" {
  type    = string
  default = ""
  validation {
    condition     = !var.provision_scaleway || length(trimspace(var.scaleway_project_name)) > 0
    error_message = "Supply the explicitly approved dedicated backup project name."
  }
}

variable "scaleway_bucket_name" {
  type    = string
  default = ""
  validation {
    condition     = !var.provision_scaleway || can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.scaleway_bucket_name))
    error_message = "Supply the approved private bucket name (3-63 lowercase alphanumeric/hyphen characters)."
  }
}

variable "scaleway_region" {
  type    = string
  default = ""
  validation {
    condition     = !var.provision_scaleway || contains(["fr-par", "nl-ams", "pl-waw"], var.scaleway_region)
    error_message = "Supply an approved supported region after verifying Standard One Zone availability there."
  }
}

variable "scaleway_runtime_name" {
  type    = string
  default = ""
  validation {
    condition     = !(var.provision_scaleway && var.provision_runtime_credentials) || length(trimspace(var.scaleway_runtime_name)) > 0
    error_message = "Supply the explicitly approved Scaleway application/policy/key description."
  }
}

variable "scaleway_admin_principals" {
  type        = list(string)
  default     = []
  description = "Verified SCW user_id/application_id principals for the active provisioner and independent administrator/recovery access; human confirmation required."
  validation {
    condition = !(var.provision_scaleway && var.provision_runtime_credentials) || (
      length(var.scaleway_admin_principals) > 0 && alltrue([
        for principal in var.scaleway_admin_principals :
        can(regex("^(user_id|application_id):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", principal))
      ])
    )
    error_message = "Confirm explicit provisioner and independent recovery SCW principals before enabling Scaleway runtime provisioning."
  }
}

provider "b2" {}

provider "scaleway" {
  organization_id = var.scaleway_organization_id != "" ? var.scaleway_organization_id : null
  region          = var.scaleway_region != "" ? var.scaleway_region : null
}

import {
  to = b2_bucket.backup
  id = "8a6f23878ab0beb4a3120e11"
}

resource "b2_bucket" "backup" {
  bucket_name = "homelab-backup-b2"
  bucket_type = "allPrivate"
  default_server_side_encryption {
    algorithm = "AES256"
    mode      = "SSE-B2"
  }
  file_lock_configuration {
    is_file_lock_enabled = false
  }
  lifecycle { prevent_destroy = true }
}

resource "b2_application_key" "runtime" {
  count        = var.provision_runtime_credentials ? 1 : 0
  key_name     = "homelab-backup-b2"
  bucket_ids   = [b2_bucket.backup.bucket_id]
  capabilities = ["listBuckets", "listFiles", "readFiles", "writeFiles", "deleteFiles"]
  lifecycle { prevent_destroy = true }
}

resource "scaleway_account_project" "backup" {
  count                 = var.provision_scaleway ? 1 : 0
  name                  = var.scaleway_project_name
  organization_id       = var.scaleway_organization_id
  delete_with_resources = false
  lifecycle { prevent_destroy = true }
}

resource "scaleway_object_bucket" "backup" {
  count               = var.provision_scaleway ? 1 : 0
  name                = var.scaleway_bucket_name
  project_id          = scaleway_account_project.backup[0].id
  region              = var.scaleway_region
  force_destroy       = false
  object_lock_enabled = false
  versioning { enabled = false }
  lifecycle { prevent_destroy = true }
}

resource "scaleway_object_bucket_acl" "backup" {
  count                 = var.provision_scaleway ? 1 : 0
  bucket                = scaleway_object_bucket.backup[0].id
  project_id            = scaleway_account_project.backup[0].id
  expected_bucket_owner = scaleway_account_project.backup[0].id
  region                = var.scaleway_region
  acl                   = "private"
  lifecycle { prevent_destroy = true }
}

resource "scaleway_iam_application" "runtime" {
  count           = var.provision_scaleway && var.provision_runtime_credentials ? 1 : 0
  name            = var.scaleway_runtime_name
  organization_id = var.scaleway_organization_id
  lifecycle { prevent_destroy = true }
}

resource "scaleway_iam_policy" "runtime" {
  count           = var.provision_scaleway && var.provision_runtime_credentials ? 1 : 0
  name            = var.scaleway_runtime_name
  organization_id = var.scaleway_organization_id
  application_id  = scaleway_iam_application.runtime[0].id
  rule {
    project_ids          = [scaleway_account_project.backup[0].id]
    permission_set_names = ["ObjectStorageReadOnly", "ObjectStorageObjectsWrite", "ObjectStorageObjectsDelete"]
  }
  lifecycle { prevent_destroy = true }
}

resource "scaleway_object_bucket_policy" "backup" {
  count      = var.provision_scaleway && var.provision_runtime_credentials ? 1 : 0
  bucket     = scaleway_object_bucket.backup[0].id
  project_id = scaleway_account_project.backup[0].id
  policy = jsonencode({
    Version = "2023-04-17"
    Statement = [
      {
        Sid       = "ProvisionerAndRecovery"
        Effect    = "Allow"
        Principal = { SCW = var.scaleway_admin_principals }
        Action    = ["s3:*"]
        Resource  = [scaleway_object_bucket.backup[0].name, "${scaleway_object_bucket.backup[0].name}/*"]
      },
      {
        Sid       = "ResticBucket"
        Effect    = "Allow"
        Principal = { SCW = "application_id:${scaleway_iam_application.runtime[0].id}" }
        Action    = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
        Resource  = [scaleway_object_bucket.backup[0].name]
      },
      {
        Sid       = "ResticObjects"
        Effect    = "Allow"
        Principal = { SCW = "application_id:${scaleway_iam_application.runtime[0].id}" }
        Action    = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource  = ["${scaleway_object_bucket.backup[0].name}/*"]
      }
    ]
  })
  depends_on = [scaleway_object_bucket_acl.backup]
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = !contains(var.scaleway_admin_principals, "application_id:${scaleway_iam_application.runtime[0].id}")
      error_message = "The runtime application must never be an administrator/recovery principal."
    }
  }
}

resource "scaleway_iam_api_key" "runtime" {
  count              = var.provision_scaleway && var.provision_runtime_credentials ? 1 : 0
  application_id     = scaleway_iam_application.runtime[0].id
  default_project_id = scaleway_account_project.backup[0].id
  description        = var.scaleway_runtime_name
  depends_on         = [scaleway_iam_policy.runtime, scaleway_object_bucket_acl.backup, scaleway_object_bucket_policy.backup]
  lifecycle { prevent_destroy = true }
}
