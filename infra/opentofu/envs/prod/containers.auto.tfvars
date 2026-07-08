containers = {
  dns = {
    vmid             = 111
    hostname         = "dns"
    description      = "AdGuard Home DNS resolver managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-dns"]
    template_file_id = "local:vztmpl/alpine-3.23-default_20260116_amd64.tar.xz"
    os_type          = "alpine"
    ip_address       = "192.168.0.3/24"
    mac_address      = "02:00:00:BA:EC:03"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 1
  }

  edge = {
    vmid             = 110
    hostname         = "edge"
    description      = "Caddy edge reverse proxy and Cloudflare DDNS managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-edge"]
    template_file_id = "local:vztmpl/alpine-3.23-default_20260116_amd64.tar.xz"
    os_type          = "alpine"
    ip_address       = "192.168.0.4/24"
    mac_address      = "02:00:00:BA:EC:04"
    gateway          = "192.168.0.1"
    root_disk_gb     = 6
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 2
  }

  tailnet = {
    vmid             = 112
    hostname         = "tailnet"
    description      = "Tailscale subnet router and optional exit node managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-tailnet"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.5/24"
    mac_address      = "02:00:00:BA:EC:05"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 3
  }

  downloads = {
    vmid             = 113
    hostname         = "downloads"
    description      = "qBittorrent over Proton WireGuard managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-downloads"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.6/24"
    mac_address      = "02:00:00:BA:EC:06"
    gateway          = "192.168.0.1"
    root_disk_gb     = 8
    cores            = 2
    memory_mb        = 1024
    swap_mb          = 512
    startup_order    = 4
  }

  files = {
    vmid             = 114
    hostname         = "files"
    description      = "Copyparty file sharing managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-files"]
    template_file_id = "local:vztmpl/alpine-3.23-default_20260116_amd64.tar.xz"
    os_type          = "alpine"
    ip_address       = "192.168.0.7/24"
    mac_address      = "02:00:00:BA:EC:07"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 1024
    swap_mb          = 0
    startup_order    = 5
  }

  minecraft = {
    vmid             = 115
    hostname         = "minecraft"
    description      = "Velocity and Paper Minecraft server managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-minecraft"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.8/24"
    mac_address      = "02:00:00:BA:EC:08"
    gateway          = "192.168.0.1"
    root_disk_gb     = 32
    cores            = 4
    memory_mb        = 4096
    swap_mb          = 1024
    startup_order    = 6
  }

  hermes = {
    vmid             = 116
    hostname         = "hermes"
    description      = "Hermes Agent Discord gateway managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-hermes"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.9/24"
    mac_address      = "02:00:00:BA:EC:09"
    gateway          = "192.168.0.1"
    root_disk_gb     = 16
    cores            = 2
    memory_mb        = 2048
    swap_mb          = 1024
    startup_order    = 7
  }

  docker_apps = {
    vmid             = 117
    hostname         = "docker-apps"
    description      = "Docker Compose app host for Traefik, media, and Minecraft managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-docker-apps"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.10/24"
    mac_address      = "02:00:00:BA:EC:0A"
    gateway          = "192.168.0.1"
    root_disk_gb     = 24
    cores            = 4
    memory_mb        = 5120
    swap_mb          = 1024
    startup_order    = 8
  }
}
