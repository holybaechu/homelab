variable "proxmox_endpoint" {
  type = string
}

variable "proxmox_api_token" {
  type      = string
  sensitive = true
}

variable "proxmox_insecure_tls" {
  type    = bool
  default = true
}

variable "proxmox_ssh_user" {
  type    = string
  default = "root"
}

variable "node_name" {
  type = string
}

variable "bridge" {
  type = string
}

variable "root_datastore_id" {
  type = string
}

variable "ssh_public_keys" {
  type = list(string)
}
