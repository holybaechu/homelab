output "containers" {
  value = {
    for name, container in module.active_lxc : name => {
      vmid     = container.vmid
      hostname = container.hostname
      ipv4     = container.ipv4
    }
  }
}
