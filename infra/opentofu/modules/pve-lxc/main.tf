resource "proxmox_virtual_environment_container" "this" {
  description   = var.description
  node_name     = var.node_name
  vm_id         = var.vmid
  unprivileged  = true
  started       = true
  start_on_boot = true
  tags          = var.tags

  cpu {
    cores = var.cores
  }

  memory {
    dedicated = var.memory_mb
    swap      = var.swap_mb
  }

  features {
    nesting = var.features.nesting
    keyctl  = var.features.keyctl
    fuse    = var.features.fuse
  }

  initialization {
    hostname = var.hostname

    dns {
      servers = ["192.168.0.11", "1.1.1.1"]
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
    name   = "veth0"
    bridge = var.bridge
  }

  disk {
    datastore_id = var.root_datastore_id
    size         = var.root_disk_gb
  }

  dynamic "mount_point" {
    for_each = var.mount_points

    content {
      volume    = mount_point.value.volume
      path      = mount_point.value.path
      read_only = mount_point.value.read_only
    }
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
