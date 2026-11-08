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
    ovh = {
      source  = "ovh/ovh"
      version = "2.23.0"
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

variable "provision_ovh" {
  type        = bool
  default     = false
  description = "Enable only after the existing project is activated, SGP availability and the separate bucket owner are verified."
}

variable "provision_runtime_credentials" {
  type        = bool
  default     = false
  description = "Enable only after an authorized administrator can securely persist generated credentials to the approved 1Password items."
}

variable "ovh_owner_user_id" {
  type        = string
  default     = ""
  description = "Verified existing project Object Storage owner user ID, separate from the runtime user; never inferred or automatically created."
}

provider "b2" {}

provider "ovh" {
  endpoint = "ovh-ca"
}

locals {
  ovh_project_id = "ca114dc8826842a093ba32f8ead79f99"
  ovh_bucket     = "homelab-backup-ovh"
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

# Read the onboarding project; never order, upgrade, rename or delete it here.
data "ovh_cloud_project" "backup" {
  service_name = local.ovh_project_id
}

resource "ovh_cloud_storage_object_bucket" "backup" {
  count         = var.provision_ovh ? 1 : 0
  service_name  = data.ovh_cloud_project.backup.service_name
  name          = local.ovh_bucket
  region        = "SGP"
  owner_user_id = var.ovh_owner_user_id
  encryption = {
    algorithm = "AES256"
  }
  versioning = {
    status = "DISABLED"
  }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition = (
        data.ovh_cloud_project.backup.status == "ok" &&
        data.ovh_cloud_project.backup.plan_code != "project.discovery" &&
        length(data.ovh_cloud_project.backup.plan_code) > 0 &&
        length(var.ovh_owner_user_id) > 0
      )
      error_message = "Stop: verify activation of the existing project and a separate owner user; IaC must not bypass discovery eligibility or billing activation."
    }
  }
}

resource "ovh_cloud_project_user" "runtime" {
  count        = var.provision_ovh && var.provision_runtime_credentials ? 1 : 0
  service_name = local.ovh_project_id
  description  = "homelab-backup-ovh"
  role_names   = ["objectstore_operator"]
  depends_on   = [ovh_cloud_storage_object_bucket.backup]
  lifecycle { prevent_destroy = true }
}

resource "ovh_cloud_project_user_s3_policy" "runtime" {
  count        = var.provision_ovh && var.provision_runtime_credentials ? 1 : 0
  service_name = local.ovh_project_id
  user_id      = ovh_cloud_project_user.runtime[0].id
  policy = jsonencode({
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
        Resource = ["arn:aws:s3:::${local.ovh_bucket}"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"]
        Resource = ["arn:aws:s3:::${local.ovh_bucket}/*"]
      },
      {
        Effect = "Deny"
        Action = [
          "s3:ListAllMyBuckets", "s3:CreateBucket", "s3:DeleteBucket",
          "s3:PutBucketAcl", "s3:PutBucketCORS", "s3:PutBucketVersioning",
          "s3:PutEncryptionConfiguration", "s3:PutLifecycleConfiguration",
          "s3:PutBucketObjectLockConfiguration", "s3:PutObjectAcl",
          "s3:PutObjectRetention", "s3:PutObjectLegalHold", "s3:BypassGovernanceRetention"
        ]
        Resource = ["*"]
      }
    ]
  })
  lifecycle { prevent_destroy = true }
}

resource "ovh_cloud_project_user_s3_credential" "runtime" {
  count        = var.provision_ovh && var.provision_runtime_credentials ? 1 : 0
  service_name = local.ovh_project_id
  user_id      = ovh_cloud_project_user.runtime[0].id
  depends_on   = [ovh_cloud_project_user_s3_policy.runtime]
  lifecycle { prevent_destroy = true }
}
