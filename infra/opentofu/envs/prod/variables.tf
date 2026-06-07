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

variable "containers" {
  type = map(object({
    vmid             = number
    hostname         = string
    description      = string
    tags             = list(string)
    template_file_id = string
    os_type          = string
    ip_address       = string
    mac_address      = string
    gateway          = string
    root_disk_gb     = number
    cores            = number
    memory_mb        = number
    swap_mb          = number
    startup_order    = number
    features = object({
      nesting = bool
      keyctl  = bool
      fuse    = bool
    })
    mount_points = list(object({
      volume    = string
      path      = string
      read_only = bool
    }))
  }))
}
