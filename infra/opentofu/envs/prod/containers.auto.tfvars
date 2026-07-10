containers = {
  tailnet = {
    vmid             = 111
    hostname         = "tailnet"
    description      = "Tailscale subnet router and exit node managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-tailnet"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.4/24"
    mac_address      = "02:00:00:BA:EC:04"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 1
  }

  docker_apps = {
    vmid             = 110
    hostname         = "docker-apps"
    description      = "Docker Compose host for all homelab application services managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-docker-apps"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.3/24"
    mac_address      = "02:00:00:BA:EC:03"
    gateway          = "192.168.0.1"
    root_disk_gb     = 32
    cores            = 6
    memory_mb        = 8192
    swap_mb          = 2048
    startup_order    = 2
  }
}
