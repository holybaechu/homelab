output "vmid" {
  value = proxmox_virtual_environment_container.this.vm_id
}

output "hostname" {
  value = var.hostname
}

output "ipv4" {
  value = var.ip_address
}
