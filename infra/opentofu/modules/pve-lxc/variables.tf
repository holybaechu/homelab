variable "node_name" {
  type = string
}

variable "vmid" {
  type = number
}

variable "hostname" {
  type = string
}

variable "description" {
  type = string
}

variable "tags" {
  type = list(string)
}

variable "template_file_id" {
  type = string
}

variable "os_type" {
  type = string
}

variable "ip_address" {
  type = string
}

variable "mac_address" {
  type = string
}

variable "gateway" {
  type = string
}

variable "bridge" {
  type = string
}

variable "root_datastore_id" {
  type = string
}

variable "root_disk_gb" {
  type = number
}

variable "cores" {
  type = number
}

variable "memory_mb" {
  type = number
}

variable "swap_mb" {
  type    = number
  default = 0
}

variable "ssh_public_keys" {
  type = list(string)
}

variable "startup_order" {
  type = number
}
