resource "proxmox_virtual_environment_container" "this" {
  description   = var.description
  node_name     = var.node_name
  vm_id         = var.vmid
  unprivileged  = true
  started       = true
  start_on_boot = true
  tags          = var.tags

  lifecycle {
    prevent_destroy = true

    ignore_changes = [
      features,
      device_passthrough,
      initialization,
      mount_point,
      operating_system,
    ]
  }

  cpu {
    cores = var.cores
  }

  memory {
    dedicated = var.memory_mb
    swap      = var.swap_mb
  }

  initialization {
    hostname = var.hostname

    dns {
      servers = ["192.168.0.3", "1.1.1.1"]
    }

    ip_config {
      ipv4 {
        address = var.ip_address
        gateway = var.gateway
      }
    }

    user_account {
      keys = var.ssh_public_keys
    }
  }

  network_interface {
    name        = "veth0"
    bridge      = var.bridge
    mac_address = var.mac_address
  }

  disk {
    datastore_id = var.root_datastore_id
    size         = var.root_disk_gb
  }

  operating_system {
    template_file_id = var.template_file_id
    type             = var.os_type
  }

  startup {
    order      = var.startup_order
    up_delay   = 15
    down_delay = 15
  }

  wait_for_ip {
    ipv4 = true
  }
}
