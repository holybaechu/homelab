module "lxc" {
  for_each = var.containers

  source = "../../modules/pve-lxc"

  node_name         = var.node_name
  bridge            = var.bridge
  root_datastore_id = var.root_datastore_id
  ssh_public_keys   = var.ssh_public_keys

  vmid             = each.value.vmid
  hostname         = each.value.hostname
  description      = each.value.description
  tags             = each.value.tags
  template_file_id = each.value.template_file_id
  os_type          = each.value.os_type
  ip_address       = each.value.ip_address
  mac_address      = each.value.mac_address
  gateway          = each.value.gateway
  root_disk_gb     = each.value.root_disk_gb
  cores            = each.value.cores
  memory_mb        = each.value.memory_mb
  swap_mb          = each.value.swap_mb
  startup_order    = each.value.startup_order
}
