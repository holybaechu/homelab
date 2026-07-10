module "active_lxc" {
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

moved {
  from = module.lxc["tailnet"]
  to   = module.active_lxc["tailnet"]
}

moved {
  from = module.lxc["docker_apps"]
  to   = module.active_lxc["docker_apps"]
}

# After the retained instances move to active_lxc, forget every legacy
# instance left at the old module address without destroying its container.
removed {
  from = module.lxc

  lifecycle {
    destroy = false
  }
}
