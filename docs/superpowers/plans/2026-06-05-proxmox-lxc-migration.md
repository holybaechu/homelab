# Proxmox LXC Homelab Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current preserved Docker Compose stacks with a Git-managed Proxmox VE LXC-native homelab using OpenTofu, Ansible, Caddy, AdGuard Home, Tailscale, qBittorrent over Proton VPN, Copyparty, and GitHub Actions CD.

**Architecture:** The repo becomes an infrastructure repository with OpenTofu defining five LXCs and Ansible configuring each LXC by role. Caddy replaces Traefik for HTTP/HTTPS edge routing; AdGuard terminates normal DNS, DoH, DoT, and DoQ as required; qBittorrent uses native Debian networking with WireGuard, NAT-PMP, and nftables; Copyparty serves completed downloads from a read-only bind mount.

**Tech Stack:** Proxmox VE, OpenTofu, bpg/proxmox provider, Ansible, Alpine/OpenRC, Debian/systemd, Caddy with Cloudflare DNS module, AdGuard Home, Tailscale, WireGuard, nftables, natpmpc, qBittorrent-nox, Copyparty, SOPS/age, GitHub Actions.

---

## Execution Notes

This is a large migration plan. Execute one task at a time and commit after each task. Do not apply OpenTofu to real Proxmox or move router port forwards until the validation task is in place and secrets are available through SOPS or GitHub Actions.

The current Windows workspace may require Git safe-directory flags. If `git status` reports dubious ownership, use:

```powershell
$repo = (Get-Location).Path
git -c "safe.directory=$repo" status --short
```

All commands below are run from repo root unless the step says otherwise.

## External Inputs Required Before Live Deployment

These values are not committed to Git. The repository can be scaffolded without them, but live deployment needs them:

- Proxmox API endpoint, node name, datastore names, bridge name, and LXC template IDs.
- Proxmox API token with LXC creation permissions.
- Proxmox SSH target for host-level bind mounts if API mount management is not used.
- Deploy SSH public/private key pair.
- Tailscale OAuth client ID, audience, and tag policy for `tag:ci`.
- Cloudflare tokens for Caddy ACME, AdGuard ACME, and DDNS.
- Proton VPN WireGuard config generated with NAT-PMP enabled.
- qBittorrent Web UI password.
- Copyparty user passwords.
- SOPS age recipient and CI age private key.

## File Structure To Create

```text
.github/
  workflows/cd.yml
apps/
  edge/
    Caddyfile
    ddns/update-cloudflare-ddns.sh
  dns/
    acme/renew-adguard-cert.sh
  downloads/
    scripts/proton_natpmp_qbt.py
  files/
    copyparty.conf
infra/
  opentofu/
    envs/prod/
    modules/pve-lxc/
  ansible/
    ansible.cfg
    inventory/prod/
    playbooks/
    roles/
scripts/
  ci/
  migration/
secrets/
tests/
  downloads/
```

## Task 1: Repository Skeleton And Guardrails

**Files:**
- Create: `.gitignore`
- Create: `apps/README.md`
- Create: `infra/README.md`
- Create: `secrets/README.md`
- Create: `scripts/migration/README.md`
- Create directories listed in "File Structure To Create"

- [ ] **Step 1: Create base directories**

Run:

```powershell
New-Item -ItemType Directory -Force -Path `
  '.github/workflows', `
  'apps/edge/ddns', `
  'apps/dns/acme', `
  'apps/downloads/scripts', `
  'apps/files', `
  'infra/opentofu/envs/prod', `
  'infra/opentofu/modules/pve-lxc', `
  'infra/ansible/inventory/prod/group_vars', `
  'infra/ansible/playbooks', `
  'infra/ansible/roles', `
  'scripts/ci', `
  'scripts/migration', `
  'secrets', `
  'tests/downloads' | Out-Null
```

Expected: command exits with code `0`.

- [ ] **Step 2: Create `.gitignore`**

Create `.gitignore` with:

```gitignore
.terraform/
*.tfstate
*.tfstate.*
*.tfplan
crash.log
crash.*.log
.terraform.lock.hcl

secrets/*.sops.yml
secrets/*.agekey
!secrets/README.md

apps/dns/AdGuardHome.yaml
apps/downloads/vpn/*.conf
apps/downloads/qbittorrent/profile/

*.retry
__pycache__/
.pytest_cache/
.venv/
```

- [ ] **Step 3: Create `apps/README.md`**

```markdown
# Apps

This directory contains service configuration that is deployed into Proxmox LXCs by Ansible.

- `edge`: Caddy and Cloudflare DDNS.
- `dns`: AdGuard Home ACME support.
- `downloads`: qBittorrent and Proton VPN helper scripts.
- `files`: Copyparty configuration.

Runtime secrets are supplied through SOPS or GitHub Actions secrets. They are not stored in this directory.
```

- [ ] **Step 4: Create `infra/README.md`**

```markdown
# Infrastructure

`opentofu` defines the Proxmox LXC shape: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and optional mount points.

`ansible` configures packages, services, templates, validation checks, and host-level bind mounts.

OpenTofu state is not committed to Git.
```

- [ ] **Step 5: Create `secrets/README.md`**

```markdown
# Secrets

Store real service secrets in SOPS-encrypted files or GitHub Actions secrets.

Expected encrypted values:

- `cloudflare_caddy_token`
- `cloudflare_adguard_acme_token`
- `cloudflare_ddns_token`
- `proton_wireguard_private_key`
- `proton_wireguard_address`
- `proton_wireguard_public_key`
- `proton_wireguard_endpoint`
- `qbittorrent_webui_password`
- `copyparty_users`
- `adguard_admin_password_hash`

Do not commit decrypted secret files.
```

- [ ] **Step 6: Create `scripts/migration/README.md`**

```markdown
# Migration Scripts

Scripts in this directory copy data from the old Docker deployment into the new LXC-native layout.

Run them only after creating service backups and before removing old Docker containers.
```

- [ ] **Step 7: Verify skeleton**

Run:

```powershell
rg --files | Sort-Object
```

Expected: output includes `.gitignore`, `apps/README.md`, `infra/README.md`, `secrets/README.md`, and `scripts/migration/README.md`.

- [ ] **Step 8: Commit**

```powershell
git add .gitignore apps infra secrets scripts
git commit -m "chore: scaffold lxc migration repository"
```

Expected: commit succeeds.

## Task 2: OpenTofu Proxmox LXC Definitions

**Files:**
- Create: `infra/opentofu/modules/pve-lxc/main.tf`
- Create: `infra/opentofu/modules/pve-lxc/variables.tf`
- Create: `infra/opentofu/modules/pve-lxc/outputs.tf`
- Create: `infra/opentofu/envs/prod/providers.tf`
- Create: `infra/opentofu/envs/prod/variables.tf`
- Create: `infra/opentofu/envs/prod/main.tf`
- Create: `infra/opentofu/envs/prod/outputs.tf`
- Create: `infra/opentofu/envs/prod/terraform.tfvars.example`

- [ ] **Step 1: Create module variables**

Create `infra/opentofu/modules/pve-lxc/variables.tf`:

```hcl
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

variable "features" {
  type = object({
    nesting = bool
    keyctl  = bool
    fuse    = bool
  })
  default = {
    nesting = false
    keyctl  = false
    fuse    = false
  }
}

variable "mount_points" {
  type = list(object({
    volume    = string
    path      = string
    read_only = bool
  }))
  default = []
}
```

- [ ] **Step 2: Create module resource**

Create `infra/opentofu/modules/pve-lxc/main.tf`:

```hcl
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
```

- [ ] **Step 3: Create module outputs**

Create `infra/opentofu/modules/pve-lxc/outputs.tf`:

```hcl
output "vmid" {
  value = proxmox_virtual_environment_container.this.vm_id
}

output "hostname" {
  value = var.hostname
}

output "ipv4" {
  value = var.ip_address
}
```

- [ ] **Step 4: Create provider config**

Create `infra/opentofu/envs/prod/providers.tf`:

```hcl
terraform {
  required_version = ">= 1.8.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.107"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = var.proxmox_insecure_tls

  ssh {
    agent    = true
    username = var.proxmox_ssh_user
  }
}
```

- [ ] **Step 5: Create prod variables**

Create `infra/opentofu/envs/prod/variables.tf`:

```hcl
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
```

- [ ] **Step 6: Create prod main module call**

Create `infra/opentofu/envs/prod/main.tf`:

```hcl
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
  gateway          = each.value.gateway
  root_disk_gb     = each.value.root_disk_gb
  cores            = each.value.cores
  memory_mb        = each.value.memory_mb
  swap_mb          = each.value.swap_mb
  startup_order    = each.value.startup_order
  features         = each.value.features
  mount_points     = each.value.mount_points
}
```

- [ ] **Step 7: Create prod outputs**

Create `infra/opentofu/envs/prod/outputs.tf`:

```hcl
output "containers" {
  value = {
    for name, container in module.lxc : name => {
      vmid     = container.vmid
      hostname = container.hostname
      ipv4     = container.ipv4
    }
  }
}
```

- [ ] **Step 8: Create example variables**

Create `infra/opentofu/envs/prod/terraform.tfvars.example`:

```hcl
proxmox_endpoint     = "https://192.168.0.2:8006/"
proxmox_api_token    = "PVEAPIToken=automation@pve!homelab=token-value-from-secret-store"
proxmox_insecure_tls = true
proxmox_ssh_user     = "root"
node_name            = "pve"
bridge               = "vmbr0"
root_datastore_id    = "local-lvm"
ssh_public_keys      = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHomelabDeployKeyMaterial homelab-deploy"]

containers = {
  dns = {
    vmid             = 111
    hostname         = "dns"
    description      = "AdGuard Home DNS resolver managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-dns"]
    template_file_id = "local:vztmpl/alpine-3.20-default_20240908_amd64.tar.xz"
    os_type          = "alpine"
    ip_address       = "192.168.0.11/24"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 1
    features         = { nesting = false, keyctl = false, fuse = false }
    mount_points     = []
  }

  edge = {
    vmid             = 110
    hostname         = "edge"
    description      = "Caddy edge reverse proxy and Cloudflare DDNS managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-edge"]
    template_file_id = "local:vztmpl/alpine-3.20-default_20240908_amd64.tar.xz"
    os_type          = "alpine"
    ip_address       = "192.168.0.10/24"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 2
    features         = { nesting = false, keyctl = false, fuse = false }
    mount_points     = []
  }

  tailnet = {
    vmid             = 112
    hostname         = "tailnet"
    description      = "Tailscale subnet router and optional exit node managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-tailnet"]
    template_file_id = "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.12/24"
    gateway          = "192.168.0.1"
    root_disk_gb     = 4
    cores            = 1
    memory_mb        = 512
    swap_mb          = 0
    startup_order    = 3
    features         = { nesting = false, keyctl = true, fuse = false }
    mount_points     = []
  }

  downloads = {
    vmid             = 113
    hostname         = "downloads"
    description      = "qBittorrent over Proton WireGuard managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-downloads"]
    template_file_id = "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.13/24"
    gateway          = "192.168.0.1"
    root_disk_gb     = 8
    cores            = 2
    memory_mb        = 2048
    swap_mb          = 512
    startup_order    = 4
    features         = { nesting = false, keyctl = true, fuse = false }
    mount_points = [
      { volume = "/tank/homelab/downloads", path = "/downloads", read_only = false }
    ]
  }

  files = {
    vmid             = 114
    hostname         = "files"
    description      = "Copyparty file sharing managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-files"]
    template_file_id = "local:vztmpl/alpine-3.20-default_20240908_amd64.tar.xz"
    os_type          = "alpine"
    ip_address       = "192.168.0.14/24"
    gateway          = "192.168.0.1"
    root_disk_gb     = 6
    cores            = 1
    memory_mb        = 1024
    swap_mb          = 0
    startup_order    = 5
    features         = { nesting = false, keyctl = false, fuse = false }
    mount_points = [
      { volume = "/tank/homelab/downloads/complete", path = "/srv/downloads", read_only = true }
    ]
  }
}
```

- [ ] **Step 9: Format and validate**

Run:

```powershell
tofu fmt -recursive infra/opentofu
tofu -chdir=infra/opentofu/envs/prod init -backend=false
tofu -chdir=infra/opentofu/envs/prod validate
```

Expected:

- `tofu fmt` exits `0`.
- `tofu init` reports successful initialization.
- `tofu validate` reports `Success! The configuration is valid.`

- [ ] **Step 10: Commit**

```powershell
git add infra/opentofu
git commit -m "feat: define proxmox lxc infrastructure"
```

Expected: commit succeeds.

## Task 3: Ansible Inventory, Common Roles, And Playbooks

**Files:**
- Create: `infra/ansible/ansible.cfg`
- Create: `infra/ansible/inventory/prod/hosts.yml`
- Create: `infra/ansible/inventory/prod/group_vars/all.yml`
- Create: `infra/ansible/inventory/prod/group_vars/alpine.yml`
- Create: `infra/ansible/inventory/prod/group_vars/debian.yml`
- Create: `infra/ansible/playbooks/bootstrap.yml`
- Create: `infra/ansible/playbooks/site.yml`
- Create role files under `infra/ansible/roles/common_alpine`
- Create role files under `infra/ansible/roles/common_debian`

- [ ] **Step 1: Create Ansible config**

Create `infra/ansible/ansible.cfg`:

```ini
[defaults]
inventory = inventory/prod/hosts.yml
host_key_checking = True
retry_files_enabled = False
stdout_callback = yaml
interpreter_python = auto_silent

[ssh_connection]
pipelining = True
ssh_args = -o ControlMaster=auto -o ControlPersist=60s
```

- [ ] **Step 2: Create inventory**

Create `infra/ansible/inventory/prod/hosts.yml`:

```yaml
all:
  vars:
    ansible_user: root
  children:
    pve_hosts:
      hosts:
        pve:
          ansible_host: 192.168.0.2
    alpine:
      hosts:
        edge:
          ansible_host: 192.168.0.10
        dns:
          ansible_host: 192.168.0.11
        files:
          ansible_host: 192.168.0.14
    debian:
      hosts:
        tailnet:
          ansible_host: 192.168.0.12
        downloads:
          ansible_host: 192.168.0.13
    edge:
      hosts:
        edge:
    dns:
      hosts:
        dns:
    tailnet:
      hosts:
        tailnet:
    downloads:
      hosts:
        downloads:
    files:
      hosts:
        files:
```

- [ ] **Step 3: Create shared variables**

Create `infra/ansible/inventory/prod/group_vars/all.yml`:

```yaml
homelab_domain: hchu.me
homelab_private_domain: home.hchu.me
homelab_lan_cidr: 192.168.0.0/24
homelab_tailscale_cidr: 100.64.0.0/10
homelab_timezone: Asia/Seoul

edge_ip: 192.168.0.10
dns_ip: 192.168.0.11
tailnet_ip: 192.168.0.12
downloads_ip: 192.168.0.13
files_ip: 192.168.0.14

service_user: homelab
service_group: homelab
service_uid: 1000
service_gid: 1000
```

Create `infra/ansible/inventory/prod/group_vars/alpine.yml`:

```yaml
common_alpine_packages:
  - ca-certificates
  - curl
  - openssh
  - tzdata
  - shadow
```

Create `infra/ansible/inventory/prod/group_vars/debian.yml`:

```yaml
common_debian_packages:
  - ca-certificates
  - curl
  - gnupg
  - openssh-server
  - python3
  - python3-apt
  - sudo
  - tzdata
```

- [ ] **Step 4: Create bootstrap playbook**

Create `infra/ansible/playbooks/bootstrap.yml`:

```yaml
---
- name: Bootstrap Alpine LXCs
  hosts: alpine
  gather_facts: false
  roles:
    - common_alpine

- name: Bootstrap Debian LXCs
  hosts: debian
  gather_facts: false
  roles:
    - common_debian
```

- [ ] **Step 5: Create site playbook**

Create `infra/ansible/playbooks/site.yml`:

```yaml
---
- name: Configure Alpine base
  hosts: alpine
  gather_facts: true
  roles:
    - common_alpine

- name: Configure Debian base
  hosts: debian
  gather_facts: true
  roles:
    - common_debian
```

- [ ] **Step 6: Create common Alpine role**

Create `infra/ansible/roles/common_alpine/tasks/main.yml`:

```yaml
---
- name: Install Alpine base packages
  community.general.apk:
    name: "{{ common_alpine_packages }}"
    update_cache: true
    state: present

- name: Set timezone
  ansible.builtin.copy:
    src: "/usr/share/zoneinfo/{{ homelab_timezone }}"
    dest: /etc/localtime
    remote_src: true
    mode: "0644"

- name: Persist timezone name
  ansible.builtin.copy:
    content: "{{ homelab_timezone }}\n"
    dest: /etc/timezone
    mode: "0644"

- name: Create service group
  ansible.builtin.group:
    name: "{{ service_group }}"
    gid: "{{ service_gid }}"
    state: present

- name: Create service user
  ansible.builtin.user:
    name: "{{ service_user }}"
    uid: "{{ service_uid }}"
    group: "{{ service_group }}"
    shell: /sbin/nologin
    create_home: false
    state: present
```

- [ ] **Step 7: Create common Debian role**

Create `infra/ansible/roles/common_debian/tasks/main.yml`:

```yaml
---
- name: Install Debian base packages
  ansible.builtin.apt:
    name: "{{ common_debian_packages }}"
    update_cache: true
    state: present

- name: Set timezone
  community.general.timezone:
    name: "{{ homelab_timezone }}"

- name: Create service group
  ansible.builtin.group:
    name: "{{ service_group }}"
    gid: "{{ service_gid }}"
    state: present

- name: Create service user
  ansible.builtin.user:
    name: "{{ service_user }}"
    uid: "{{ service_uid }}"
    group: "{{ service_group }}"
    shell: /usr/sbin/nologin
    create_home: false
    state: present
```

- [ ] **Step 8: Create Ansible requirements**

Create `infra/ansible/requirements.yml`:

```yaml
---
collections:
  - name: community.general
    version: ">=9.0.0"
```

- [ ] **Step 9: Syntax check**

Run:

```powershell
ansible-galaxy collection install -r infra/ansible/requirements.yml
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/bootstrap.yml --syntax-check
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
```

Expected: both syntax checks report `playbook:`.

- [ ] **Step 10: Commit**

```powershell
git add infra/ansible
git commit -m "feat: add ansible base inventory and roles"
```

Expected: commit succeeds.

## Task 4: Edge LXC With Caddy And DDNS

**Files:**
- Create: `apps/edge/Caddyfile`
- Create: `apps/edge/ddns/update-cloudflare-ddns.sh`
- Create: `infra/ansible/inventory/prod/group_vars/edge.yml`
- Create: `infra/ansible/roles/caddy/tasks/main.yml`
- Create: `infra/ansible/roles/caddy/handlers/main.yml`
- Create: `infra/ansible/roles/caddy/templates/caddy.openrc.j2`
- Create: `infra/ansible/roles/caddy/templates/caddy.env.j2`
- Create: `infra/ansible/roles/ddns/tasks/main.yml`
- Create: `infra/ansible/roles/ddns/templates/ddns.openrc.j2`
- Modify: `infra/ansible/playbooks/site.yml`

- [ ] **Step 1: Create edge variables**

Create `infra/ansible/inventory/prod/group_vars/edge.yml`:

```yaml
caddy_version: "v2.11.3"
xcaddy_version: "v0.4.4"
caddy_cloudflare_module: "github.com/caddy-dns/cloudflare"
caddy_config_path: /etc/caddy/Caddyfile
caddy_env_path: /etc/conf.d/caddy
caddy_data_dir: /var/lib/caddy
caddy_config_dir: /etc/caddy

ddns_record_names:
  - copyparty.hchu.me
  - dns.hchu.me
ddns_interval_seconds: 300
```

- [ ] **Step 2: Create Caddyfile**

Create `apps/edge/Caddyfile`:

```caddyfile
{
	email holybaechu@proton.me
	acme_dns cloudflare {$CLOUDFLARE_DNS_API_TOKEN}
}

(private_only) {
	@not_private not remote_ip 192.168.0.0/24 100.64.0.0/10
	respond @not_private 403
}

(secure_headers) {
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer-when-downgrade"
	}
}

copyparty.hchu.me {
	import secure_headers
	reverse_proxy 192.168.0.14:3923
}

adguard.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy 192.168.0.11:80
}

qbt.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy 192.168.0.13:8080
}

pve.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy https://192.168.0.2:8006 {
		transport http {
			tls_insecure_skip_verify
		}
	}
}

router.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy http://192.168.0.1
}

printer.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy https://192.168.0.4 {
		transport http {
			tls_insecure_skip_verify
		}
	}
}

dns.hchu.me {
	import secure_headers
	handle /dns-query {
		reverse_proxy https://192.168.0.11:443 {
			transport http {
				tls_insecure_skip_verify
			}
		}
	}
	respond 404
}
```

- [ ] **Step 3: Create Caddy OpenRC template**

Create `infra/ansible/roles/caddy/templates/caddy.openrc.j2`:

```sh
#!/sbin/openrc-run

name="caddy"
description="Caddy web server"
command="/usr/local/bin/caddy"
command_args="run --config {{ caddy_config_path }} --adapter caddyfile"
command_background="yes"
pidfile="/run/caddy.pid"
command_user="{{ service_user }}:{{ service_group }}"
output_log="/var/log/caddy.log"
error_log="/var/log/caddy.err"

depend() {
	need net
	after firewall
}

start_pre() {
	checkpath --directory --owner {{ service_user }}:{{ service_group }} --mode 0750 {{ caddy_data_dir }}
	checkpath --directory --owner {{ service_user }}:{{ service_group }} --mode 0750 /var/log
	/usr/local/bin/caddy validate --config {{ caddy_config_path }} --adapter caddyfile
}
```

- [ ] **Step 4: Create Caddy environment template**

Create `infra/ansible/roles/caddy/templates/caddy.env.j2`:

```sh
CLOUDFLARE_DNS_API_TOKEN="{{ cloudflare_caddy_token }}"
XDG_DATA_HOME="{{ caddy_data_dir }}"
XDG_CONFIG_HOME="{{ caddy_config_dir }}"
```

- [ ] **Step 5: Create Caddy role**

Create `infra/ansible/roles/caddy/tasks/main.yml`:

```yaml
---
- name: Install Caddy build packages
  community.general.apk:
    name:
      - go
      - git
      - libcap
    update_cache: true
    state: present

- name: Install xcaddy
  ansible.builtin.command:
    cmd: "go install github.com/caddyserver/xcaddy/cmd/xcaddy@{{ xcaddy_version }}"
    creates: /root/go/bin/xcaddy

- name: Build Caddy with Cloudflare DNS module
  ansible.builtin.command:
    cmd: "/root/go/bin/xcaddy build {{ caddy_version }} --with {{ caddy_cloudflare_module }}"
    chdir: /tmp
    creates: /tmp/caddy

- name: Install Caddy binary
  ansible.builtin.copy:
    src: /tmp/caddy
    dest: /usr/local/bin/caddy
    remote_src: true
    mode: "0755"
  notify: Restart caddy

- name: Allow Caddy to bind privileged ports
  ansible.builtin.command:
    cmd: setcap cap_net_bind_service=+ep /usr/local/bin/caddy
  changed_when: false

- name: Create Caddy directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0750"
  loop:
    - "{{ caddy_config_dir }}"
    - "{{ caddy_data_dir }}"

- name: Install Caddyfile
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../../../apps/edge/Caddyfile"
    dest: "{{ caddy_config_path }}"
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0640"
  notify: Restart caddy

- name: Install Caddy environment
  ansible.builtin.template:
    src: caddy.env.j2
    dest: "{{ caddy_env_path }}"
    owner: root
    group: root
    mode: "0600"
  notify: Restart caddy

- name: Install Caddy OpenRC service
  ansible.builtin.template:
    src: caddy.openrc.j2
    dest: /etc/init.d/caddy
    mode: "0755"
  notify: Restart caddy

- name: Enable Caddy
  ansible.builtin.service:
    name: caddy
    enabled: true
    state: started
```

Create `infra/ansible/roles/caddy/handlers/main.yml`:

```yaml
---
- name: Restart caddy
  ansible.builtin.service:
    name: caddy
    state: restarted
```

- [ ] **Step 6: Create DDNS updater**

Create `apps/edge/ddns/update-cloudflare-ddns.sh`:

```sh
#!/bin/sh
set -eu

ZONE_ID="${CLOUDFLARE_ZONE_ID}"
TOKEN="${CLOUDFLARE_DDNS_TOKEN}"
RECORDS="${DDNS_RECORD_NAMES}"

PUBLIC_IP="$(curl -fsS https://api.ipify.org)"

for RECORD in ${RECORDS}; do
  RECORD_ID="$(curl -fsS \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?type=A&name=${RECORD}" \
    | sed -n 's/.*"id":"\([^"]*\)".*/\1/p' | head -n 1)"

  if [ -z "${RECORD_ID}" ]; then
    echo "No Cloudflare A record found for ${RECORD}" >&2
    exit 1
  fi

  curl -fsS -X PATCH \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data "{\"type\":\"A\",\"name\":\"${RECORD}\",\"content\":\"${PUBLIC_IP}\",\"ttl\":120,\"proxied\":false}" \
    "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records/${RECORD_ID}" >/dev/null

  echo "Updated ${RECORD} to ${PUBLIC_IP}"
done
```

- [ ] **Step 7: Create DDNS OpenRC template**

Create `infra/ansible/roles/ddns/templates/ddns.openrc.j2`:

```sh
#!/sbin/openrc-run

name="cloudflare-ddns"
description="Cloudflare DDNS update loop"
command="/usr/local/bin/cloudflare-ddns-loop"
command_background="yes"
pidfile="/run/cloudflare-ddns.pid"
output_log="/var/log/cloudflare-ddns.log"
error_log="/var/log/cloudflare-ddns.err"

depend() {
	need net
}
```

- [ ] **Step 8: Create DDNS role**

Create `infra/ansible/roles/ddns/tasks/main.yml`:

```yaml
---
- name: Install DDNS script
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../../../apps/edge/ddns/update-cloudflare-ddns.sh"
    dest: /usr/local/bin/update-cloudflare-ddns
    owner: root
    group: root
    mode: "0700"

- name: Install DDNS loop
  ansible.builtin.copy:
    dest: /usr/local/bin/cloudflare-ddns-loop
    owner: root
    group: root
    mode: "0700"
    content: |
      #!/bin/sh
      set -eu
      export CLOUDFLARE_ZONE_ID="{{ cloudflare_zone_id }}"
      export CLOUDFLARE_DDNS_TOKEN="{{ cloudflare_ddns_token }}"
      export DDNS_RECORD_NAMES="{{ ddns_record_names | join(' ') }}"
      while true; do
        /usr/local/bin/update-cloudflare-ddns
        sleep {{ ddns_interval_seconds }}
      done

- name: Install DDNS OpenRC service
  ansible.builtin.template:
    src: ddns.openrc.j2
    dest: /etc/init.d/cloudflare-ddns
    mode: "0755"

- name: Enable DDNS
  ansible.builtin.service:
    name: cloudflare-ddns
    enabled: true
    state: started
```

- [ ] **Step 9: Add edge roles to site playbook**

Modify `infra/ansible/playbooks/site.yml` to:

```yaml
---
- name: Configure Alpine base
  hosts: alpine
  gather_facts: true
  roles:
    - common_alpine

- name: Configure Debian base
  hosts: debian
  gather_facts: true
  roles:
    - common_debian

- name: Configure edge LXC
  hosts: edge
  gather_facts: true
  roles:
    - caddy
    - ddns
```

- [ ] **Step 10: Syntax and local config checks**

Run:

```powershell
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
```

Expected: syntax check reports `playbook:`.

- [ ] **Step 11: Commit**

```powershell
git add apps/edge infra/ansible
git commit -m "feat: add caddy edge and ddns roles"
```

Expected: commit succeeds.

## Task 5: DNS LXC With AdGuard Home And Dedicated ACME

**Files:**
- Create: `apps/dns/acme/renew-adguard-cert.sh`
- Create: `infra/ansible/inventory/prod/group_vars/dns.yml`
- Create: `infra/ansible/roles/adguard/tasks/main.yml`
- Create: `infra/ansible/roles/adguard/templates/adguard.openrc.j2`
- Create: `infra/ansible/roles/adguard/templates/AdGuardHome.yaml.j2`
- Create: `infra/ansible/roles/adguard_acme/tasks/main.yml`
- Create: `infra/ansible/roles/adguard_acme/templates/adguard-acme.openrc.j2`
- Create: `scripts/migration/backup-adguard.sh`
- Create: `scripts/migration/restore-adguard-to-lxc.sh`
- Modify: `infra/ansible/playbooks/site.yml`

- [ ] **Step 1: Create DNS variables**

Create `infra/ansible/inventory/prod/group_vars/dns.yml`:

```yaml
adguard_version: "v0.107.76"
adguard_arch: "linux_amd64"
adguard_install_dir: /opt/adguardhome
adguard_work_dir: /opt/adguardhome/work
adguard_conf_dir: /opt/adguardhome/conf
adguard_tls_dir: /opt/adguardhome/tls
adguard_cert_domain: dns.hchu.me
adguard_admin_port: 80
adguard_https_port: 443
adguard_dns_port: 53
adguard_dot_port: 853
```

- [ ] **Step 2: Create AdGuard OpenRC template**

Create `infra/ansible/roles/adguard/templates/adguard.openrc.j2`:

```sh
#!/sbin/openrc-run

name="AdGuardHome"
description="AdGuard Home DNS resolver"
command="{{ adguard_install_dir }}/AdGuardHome"
command_args="-s run -w {{ adguard_work_dir }} -c {{ adguard_conf_dir }}/AdGuardHome.yaml"
command_background="yes"
pidfile="/run/adguardhome.pid"
output_log="/var/log/adguardhome.log"
error_log="/var/log/adguardhome.err"

depend() {
	need net
}

start_pre() {
	checkpath --directory --owner {{ service_user }}:{{ service_group }} --mode 0750 {{ adguard_work_dir }}
	checkpath --directory --owner {{ service_user }}:{{ service_group }} --mode 0750 {{ adguard_conf_dir }}
	checkpath --directory --owner {{ service_user }}:{{ service_group }} --mode 0750 {{ adguard_tls_dir }}
}
```

- [ ] **Step 3: Create AdGuard baseline config template**

Create `infra/ansible/roles/adguard/templates/AdGuardHome.yaml.j2`:

```yaml
bind_host: 0.0.0.0
bind_port: {{ adguard_admin_port }}
users:
  - name: admin
    password: "{{ adguard_admin_password_hash }}"
auth_attempts: 5
block_auth_min: 15
http_proxy: ""
language: en
theme: auto
dns:
  bind_hosts:
    - 0.0.0.0
  port: {{ adguard_dns_port }}
  anonymize_client_ip: false
  ratelimit: 20
  upstream_dns:
    - https://dns10.quad9.net/dns-query
    - https://cloudflare-dns.com/dns-query
  bootstrap_dns:
    - 1.1.1.1
    - 9.9.9.9
  protection_enabled: true
tls:
  enabled: true
  server_name: {{ adguard_cert_domain }}
  force_https: false
  port_https: {{ adguard_https_port }}
  port_dns_over_tls: {{ adguard_dot_port }}
  port_dns_over_quic: {{ adguard_dot_port }}
  certificate_path: "{{ adguard_tls_dir }}/fullchain.pem"
  private_key_path: "{{ adguard_tls_dir }}/privkey.pem"
trusted_proxies:
  - "{{ edge_ip }}/32"
filters:
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt
    name: AdGuard DNS filter
    id: 1
```

- [ ] **Step 4: Create AdGuard role**

Create `infra/ansible/roles/adguard/tasks/main.yml`:

```yaml
---
- name: Install AdGuard dependencies
  community.general.apk:
    name:
      - ca-certificates
      - curl
      - ldns
      - tar
    update_cache: true
    state: present

- name: Create AdGuard directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0750"
  loop:
    - "{{ adguard_install_dir }}"
    - "{{ adguard_work_dir }}"
    - "{{ adguard_conf_dir }}"
    - "{{ adguard_tls_dir }}"

- name: Download AdGuard Home
  ansible.builtin.get_url:
    url: "https://github.com/AdguardTeam/AdGuardHome/releases/download/{{ adguard_version }}/AdGuardHome_{{ adguard_arch }}.tar.gz"
    dest: /tmp/AdGuardHome.tar.gz
    mode: "0644"

- name: Extract AdGuard Home
  ansible.builtin.unarchive:
    src: /tmp/AdGuardHome.tar.gz
    dest: /tmp
    remote_src: true
    creates: /tmp/AdGuardHome/AdGuardHome

- name: Install AdGuard binary
  ansible.builtin.copy:
    src: /tmp/AdGuardHome/AdGuardHome
    dest: "{{ adguard_install_dir }}/AdGuardHome"
    remote_src: true
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0755"

- name: Install baseline AdGuard config when no migrated config exists
  ansible.builtin.template:
    src: AdGuardHome.yaml.j2
    dest: "{{ adguard_conf_dir }}/AdGuardHome.yaml"
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0640"
    force: false

- name: Install AdGuard OpenRC service
  ansible.builtin.template:
    src: adguard.openrc.j2
    dest: /etc/init.d/adguardhome
    mode: "0755"

- name: Start AdGuard when configuration exists
  ansible.builtin.service:
    name: adguardhome
    enabled: true
    state: started
  when: ansible_facts['distribution'] == 'Alpine'
```

- [ ] **Step 5: Create AdGuard ACME script**

Create `apps/dns/acme/renew-adguard-cert.sh`:

```sh
#!/bin/sh
set -eu

export CF_DNS_API_TOKEN="${CLOUDFLARE_ADGUARD_ACME_TOKEN}"

DOMAIN="${ADGUARD_CERT_DOMAIN:-dns.hchu.me}"
CERT_DIR="${ADGUARD_TLS_DIR:-/opt/adguardhome/tls}"

mkdir -p "${CERT_DIR}"

lego \
  --dns cloudflare \
  --domains "${DOMAIN}" \
  --email "holybaechu@proton.me" \
  --path /var/lib/lego \
  run || lego \
  --dns cloudflare \
  --domains "${DOMAIN}" \
  --email "holybaechu@proton.me" \
  --path /var/lib/lego \
  renew --days 30

cp "/var/lib/lego/certificates/${DOMAIN}.crt" "${CERT_DIR}/fullchain.pem"
cp "/var/lib/lego/certificates/${DOMAIN}.key" "${CERT_DIR}/privkey.pem"
chown homelab:homelab "${CERT_DIR}/fullchain.pem" "${CERT_DIR}/privkey.pem"
chmod 0640 "${CERT_DIR}/fullchain.pem" "${CERT_DIR}/privkey.pem"

rc-service adguardhome restart || true
```

- [ ] **Step 6: Create AdGuard ACME OpenRC template**

Create `infra/ansible/roles/adguard_acme/templates/adguard-acme.openrc.j2`:

```sh
#!/sbin/openrc-run

name="adguard-acme"
description="AdGuard Home DNS-01 certificate renewal loop"
command="/usr/local/bin/adguard-acme-loop"
command_background="yes"
pidfile="/run/adguard-acme.pid"
output_log="/var/log/adguard-acme.log"
error_log="/var/log/adguard-acme.err"

depend() {
	need net
	after adguardhome
}
```

- [ ] **Step 7: Create AdGuard ACME role**

Create `infra/ansible/roles/adguard_acme/tasks/main.yml`:

```yaml
---
- name: Install lego
  community.general.apk:
    name: lego
    update_cache: true
    state: present

- name: Create lego data directory
  ansible.builtin.file:
    path: /var/lib/lego
    state: directory
    owner: root
    group: root
    mode: "0700"

- name: Install AdGuard ACME script
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../../../apps/dns/acme/renew-adguard-cert.sh"
    dest: /usr/local/bin/renew-adguard-cert
    owner: root
    group: root
    mode: "0700"

- name: Install AdGuard ACME loop
  ansible.builtin.copy:
    dest: /usr/local/bin/adguard-acme-loop
    owner: root
    group: root
    mode: "0700"
    content: |
      #!/bin/sh
      set -eu
      export CLOUDFLARE_ADGUARD_ACME_TOKEN="{{ cloudflare_adguard_acme_token }}"
      export ADGUARD_CERT_DOMAIN="{{ adguard_cert_domain }}"
      export ADGUARD_TLS_DIR="{{ adguard_tls_dir }}"
      while true; do
        /usr/local/bin/renew-adguard-cert
        sleep 43200
      done

- name: Issue initial AdGuard certificate
  ansible.builtin.command:
    cmd: /usr/local/bin/renew-adguard-cert
    creates: "{{ adguard_tls_dir }}/fullchain.pem"
  environment:
    CLOUDFLARE_ADGUARD_ACME_TOKEN: "{{ cloudflare_adguard_acme_token }}"
    ADGUARD_CERT_DOMAIN: "{{ adguard_cert_domain }}"
    ADGUARD_TLS_DIR: "{{ adguard_tls_dir }}"
  no_log: true

- name: Install AdGuard ACME OpenRC service
  ansible.builtin.template:
    src: adguard-acme.openrc.j2
    dest: /etc/init.d/adguard-acme
    mode: "0755"

- name: Enable AdGuard ACME
  ansible.builtin.service:
    name: adguard-acme
    enabled: true
    state: started
```

- [ ] **Step 8: Create AdGuard backup script**

Create `scripts/migration/backup-adguard.sh`:

```sh
#!/bin/sh
set -eu

OLD_HOST="${OLD_DOCKER_HOST}"
BACKUP_DIR="${BACKUP_DIR:-./migration-backups/adguard}"

mkdir -p "${BACKUP_DIR}"

rsync -a --delete "${OLD_HOST}:/home/docker/data/adguard/conf/" "${BACKUP_DIR}/conf/"
rsync -a --delete "${OLD_HOST}:/home/docker/data/adguard/work/" "${BACKUP_DIR}/work/"

tar -C "${BACKUP_DIR}/.." -czf "${BACKUP_DIR}.tar.gz" adguard
echo "AdGuard backup written to ${BACKUP_DIR}.tar.gz"
```

- [ ] **Step 9: Create AdGuard restore script**

Create `scripts/migration/restore-adguard-to-lxc.sh`:

```sh
#!/bin/sh
set -eu

DNS_LXC="${DNS_LXC:-root@192.168.0.11}"
BACKUP_DIR="${BACKUP_DIR:-./migration-backups/adguard}"

rsync -a "${BACKUP_DIR}/conf/" "${DNS_LXC}:/opt/adguardhome/conf/"
rsync -a "${BACKUP_DIR}/work/" "${DNS_LXC}:/opt/adguardhome/work/"
ssh "${DNS_LXC}" "chown -R homelab:homelab /opt/adguardhome/conf /opt/adguardhome/work && rc-service adguardhome restart"
```

- [ ] **Step 10: Add DNS roles to site playbook**

Modify `infra/ansible/playbooks/site.yml` so it includes:

```yaml
- name: Configure DNS LXC
  hosts: dns
  gather_facts: true
  roles:
    - adguard_acme
    - adguard
```

- [ ] **Step 11: Syntax check and script checks**

Run:

```powershell
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
```

Expected: syntax check reports `playbook:`.

- [ ] **Step 12: Commit**

```powershell
git add apps/dns infra/ansible scripts/migration
git commit -m "feat: add adguard dns role and migration scripts"
```

Expected: commit succeeds.

## Task 6: Tailnet LXC With Native Tailscale

**Files:**
- Create: `infra/ansible/inventory/prod/group_vars/tailnet.yml`
- Create: `infra/ansible/roles/tailscale_gateway/tasks/main.yml`
- Modify: `infra/ansible/playbooks/site.yml`

- [ ] **Step 1: Create tailnet variables**

Create `infra/ansible/inventory/prod/group_vars/tailnet.yml`:

```yaml
tailscale_advertise_routes: "192.168.0.0/24"
tailscale_advertise_exit_node: true
tailscale_accept_routes: true
tailscale_hostname: homelab-tailnet
```

- [ ] **Step 2: Create Tailscale role**

Create `infra/ansible/roles/tailscale_gateway/tasks/main.yml`:

```yaml
---
- name: Install Tailscale signing key
  ansible.builtin.get_url:
    url: https://pkgs.tailscale.com/stable/debian/bookworm.noarmor.gpg
    dest: /usr/share/keyrings/tailscale-archive-keyring.gpg
    mode: "0644"

- name: Install Tailscale repository
  ansible.builtin.copy:
    dest: /etc/apt/sources.list.d/tailscale.list
    mode: "0644"
    content: |
      deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian bookworm main

- name: Install Tailscale
  ansible.builtin.apt:
    name: tailscale
    update_cache: true
    state: present

- name: Enable IPv4 forwarding
  ansible.builtin.copy:
    dest: /etc/sysctl.d/99-tailscale-subnet-router.conf
    mode: "0644"
    content: |
      net.ipv4.ip_forward=1
      net.ipv6.conf.all.forwarding=1

- name: Apply sysctl
  ansible.builtin.command:
    cmd: sysctl --system
  changed_when: false

- name: Enable tailscaled
  ansible.builtin.systemd:
    name: tailscaled
    enabled: true
    state: started

- name: Join Tailscale when auth key is supplied
  ansible.builtin.command:
    cmd: >
      tailscale up
      --hostname={{ tailscale_hostname }}
      --accept-routes={{ tailscale_accept_routes | lower }}
      --advertise-routes={{ tailscale_advertise_routes }}
      {{ '--advertise-exit-node' if tailscale_advertise_exit_node else '' }}
      --auth-key={{ tailscale_auth_key }}
  no_log: true
  when: tailscale_auth_key is defined
  changed_when: true
```

- [ ] **Step 3: Add tailnet role to site playbook**

Modify `infra/ansible/playbooks/site.yml` so it includes:

```yaml
- name: Configure tailnet LXC
  hosts: tailnet
  gather_facts: true
  roles:
    - tailscale_gateway
```

- [ ] **Step 4: Syntax check**

Run:

```powershell
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
```

Expected: syntax check reports `playbook:`.

- [ ] **Step 5: Commit**

```powershell
git add infra/ansible
git commit -m "feat: add tailscale subnet router role"
```

Expected: commit succeeds.

## Task 7: Downloads LXC With qBittorrent, WireGuard, NAT-PMP, And Killswitch

**Files:**
- Create: `requirements-dev.txt`
- Create: `apps/downloads/scripts/proton_natpmp_qbt.py`
- Create: `tests/downloads/test_proton_natpmp_qbt.py`
- Create: `infra/ansible/inventory/prod/group_vars/downloads.yml`
- Create: `infra/ansible/roles/downloads_vpn/tasks/main.yml`
- Create: `infra/ansible/roles/downloads_vpn/templates/wg-proton.conf.j2`
- Create: `infra/ansible/roles/downloads_vpn/templates/nftables.conf.j2`
- Create: `infra/ansible/roles/qbittorrent/tasks/main.yml`
- Create: `infra/ansible/roles/qbittorrent/templates/qbittorrent.service.j2`
- Create: `infra/ansible/roles/qbittorrent/templates/qBittorrent.conf.j2`
- Create: `scripts/migration/backup-qbittorrent.sh`
- Modify: `infra/ansible/playbooks/site.yml`

- [ ] **Step 1: Create Python parser test**

Create `requirements-dev.txt`:

```text
pytest>=8.0.0
```

Create `tests/downloads/test_proton_natpmp_qbt.py`:

```python
from apps.downloads.scripts.proton_natpmp_qbt import parse_natpmp_port


def test_parse_natpmp_port_from_mapping_output():
    output = """
    initnatpmp() returned 0 (SUCCESS)
    using gateway : 10.2.0.1
    Mapped public port 53186 protocol TCP to local port 1 lifetime 60
    epoch = 123456
    """
    assert parse_natpmp_port(output) == 53186


def test_parse_natpmp_port_rejects_missing_mapping():
    output = "initnatpmp() returned 0 (SUCCESS)"
    try:
        parse_natpmp_port(output)
    except ValueError as exc:
        assert "public port" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run test and confirm failure**

Run:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests/downloads/test_proton_natpmp_qbt.py -q
```

Expected: failure because `apps.downloads.scripts.proton_natpmp_qbt` does not exist.

- [ ] **Step 3: Create package markers**

Create empty files:

```powershell
New-Item -ItemType File -Force -Path 'apps/__init__.py', 'apps/downloads/__init__.py', 'apps/downloads/scripts/__init__.py' | Out-Null
```

- [ ] **Step 4: Create NAT-PMP updater script**

Create `apps/downloads/scripts/proton_natpmp_qbt.py`:

```python
#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import urllib.parse
import urllib.request


def parse_natpmp_port(output: str) -> int:
    match = re.search(r"Mapped public port\s+(\d+)\s+protocol\s+TCP", output)
    if not match:
        raise ValueError("natpmpc output did not include a TCP public port")
    return int(match.group(1))


def run_natpmp(gateway: str) -> int:
    udp = subprocess.run(
        ["natpmpc", "-a", "1", "0", "udp", "60", "-g", gateway],
        check=True,
        text=True,
        capture_output=True,
    )
    tcp = subprocess.run(
        ["natpmpc", "-a", "1", "0", "tcp", "60", "-g", gateway],
        check=True,
        text=True,
        capture_output=True,
    )
    print(udp.stdout)
    print(tcp.stdout)
    return parse_natpmp_port(tcp.stdout)


def qbittorrent_login(base_url: str, username: str, password: str) -> str:
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    request = urllib.request.Request(f"{base_url}/api/v2/auth/login", data=data)
    with urllib.request.urlopen(request, timeout=10) as response:
        cookie = response.headers.get("Set-Cookie")
        if not cookie:
            raise RuntimeError("qBittorrent login did not return a session cookie")
        return cookie


def qbittorrent_set_port(base_url: str, cookie: str, port: int) -> None:
    payload = {"listen_port": port, "upnp": False, "random_port": False}
    data = urllib.parse.urlencode({"json": json.dumps(payload)}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/v2/app/setPreferences",
        data=data,
        headers={"Cookie": cookie},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"qBittorrent setPreferences returned {response.status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="10.2.0.1")
    parser.add_argument("--qbt-url", default="http://127.0.0.1:8080")
    parser.add_argument("--qbt-user", default="admin")
    parser.add_argument("--qbt-password", required=True)
    args = parser.parse_args()

    port = run_natpmp(args.gateway)
    cookie = qbittorrent_login(args.qbt_url, args.qbt_user, args.qbt_password)
    qbittorrent_set_port(args.qbt_url, cookie, port)
    print(f"Updated qBittorrent listen port to {port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run parser test and confirm pass**

Run:

```powershell
python -m pytest tests/downloads/test_proton_natpmp_qbt.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Create downloads variables**

Create `infra/ansible/inventory/prod/group_vars/downloads.yml`:

```yaml
downloads_mount_path: /downloads
downloads_complete_path: /downloads/complete
downloads_incomplete_path: /downloads/incomplete
proton_wg_interface: wg-proton
proton_natpmp_gateway: 10.2.0.1
qbittorrent_webui_port: 8080
qbittorrent_user: qbittorrent
qbittorrent_group: qbittorrent
qbittorrent_uid: 1100
qbittorrent_gid: 1100
```

- [ ] **Step 7: Create WireGuard template**

Create `infra/ansible/roles/downloads_vpn/templates/wg-proton.conf.j2`:

```ini
[Interface]
PrivateKey = {{ proton_wireguard_private_key }}
Address = {{ proton_wireguard_address }}
DNS = 1.1.1.1

[Peer]
PublicKey = {{ proton_wireguard_public_key }}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {{ proton_wireguard_endpoint }}
PersistentKeepalive = 25
```

- [ ] **Step 8: Create nftables killswitch template**

Create `infra/ansible/roles/downloads_vpn/templates/nftables.conf.j2`:

```nft
flush ruleset

table inet filter {
  chain output {
    type filter hook output priority 0; policy accept;

    oif "lo" accept
    ip daddr {{ homelab_lan_cidr }} accept
    ip daddr 100.64.0.0/10 accept
    udp dport 53 accept
    tcp dport 53 accept
    udp dport 51820 accept

    meta skuid {{ qbittorrent_uid }} oifname "{{ proton_wg_interface }}" accept
    meta skuid {{ qbittorrent_uid }} reject
  }
}
```

- [ ] **Step 9: Create downloads VPN role**

Create `infra/ansible/roles/downloads_vpn/tasks/main.yml`:

```yaml
---
- name: Install VPN packages
  ansible.builtin.apt:
    name:
      - wireguard-tools
      - nftables
      - natpmpc
      - python3
    update_cache: true
    state: present

- name: Install WireGuard Proton config
  ansible.builtin.template:
    src: wg-proton.conf.j2
    dest: "/etc/wireguard/{{ proton_wg_interface }}.conf"
    owner: root
    group: root
    mode: "0600"
  no_log: true

- name: Install nftables config
  ansible.builtin.template:
    src: nftables.conf.j2
    dest: /etc/nftables.conf
    owner: root
    group: root
    mode: "0644"

- name: Enable nftables
  ansible.builtin.systemd:
    name: nftables
    enabled: true
    state: restarted

- name: Enable WireGuard
  ansible.builtin.systemd:
    name: "wg-quick@{{ proton_wg_interface }}"
    enabled: true
    state: started
```

- [ ] **Step 10: Create qBittorrent config template**

Create `infra/ansible/roles/qbittorrent/templates/qBittorrent.conf.j2`:

```ini
[Preferences]
WebUI\Address=0.0.0.0
WebUI\Port={{ qbittorrent_webui_port }}
WebUI\Username=admin
WebUI\Password_PBKDF2="{{ qbittorrent_webui_password_hash }}"
Downloads\SavePath={{ downloads_complete_path }}/
Downloads\TempPath={{ downloads_incomplete_path }}/
Downloads\TempPathEnabled=true
Connection\Interface={{ proton_wg_interface }}
Connection\PortRangeMin=0
Connection\UPnP=false
Connection\RandomPort=false
```

- [ ] **Step 11: Create qBittorrent systemd template**

Create `infra/ansible/roles/qbittorrent/templates/qbittorrent.service.j2`:

```ini
[Unit]
Description=qBittorrent-nox service
After=network-online.target wg-quick@{{ proton_wg_interface }}.service nftables.service
Wants=network-online.target
Requires=wg-quick@{{ proton_wg_interface }}.service nftables.service

[Service]
Type=simple
User={{ qbittorrent_user }}
Group={{ qbittorrent_group }}
ExecStart=/usr/bin/qbittorrent-nox --webui-port={{ qbittorrent_webui_port }}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 12: Create qBittorrent role**

Create `infra/ansible/roles/qbittorrent/tasks/main.yml`:

```yaml
---
- name: Install qBittorrent
  ansible.builtin.apt:
    name:
      - qbittorrent-nox
      - python3
    update_cache: true
    state: present

- name: Create qBittorrent group
  ansible.builtin.group:
    name: "{{ qbittorrent_group }}"
    gid: "{{ qbittorrent_gid }}"
    state: present

- name: Create qBittorrent user
  ansible.builtin.user:
    name: "{{ qbittorrent_user }}"
    uid: "{{ qbittorrent_uid }}"
    group: "{{ qbittorrent_group }}"
    shell: /usr/sbin/nologin
    create_home: true
    home: /var/lib/qbittorrent
    state: present

- name: Create download directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ qbittorrent_user }}"
    group: "{{ qbittorrent_group }}"
    mode: "0775"
  loop:
    - "{{ downloads_mount_path }}"
    - "{{ downloads_complete_path }}"
    - "{{ downloads_incomplete_path }}"

- name: Create qBittorrent config directory
  ansible.builtin.file:
    path: /var/lib/qbittorrent/.config/qBittorrent
    state: directory
    owner: "{{ qbittorrent_user }}"
    group: "{{ qbittorrent_group }}"
    mode: "0700"

- name: Install qBittorrent config
  ansible.builtin.template:
    src: qBittorrent.conf.j2
    dest: /var/lib/qbittorrent/.config/qBittorrent/qBittorrent.conf
    owner: "{{ qbittorrent_user }}"
    group: "{{ qbittorrent_group }}"
    mode: "0600"
  no_log: true

- name: Install NAT-PMP updater
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../../../apps/downloads/scripts/proton_natpmp_qbt.py"
    dest: /usr/local/bin/proton-natpmp-qbt
    owner: root
    group: root
    mode: "0755"

- name: Install NAT-PMP systemd service
  ansible.builtin.copy:
    dest: /etc/systemd/system/proton-natpmp-qbt.service
    mode: "0644"
    content: |
      [Unit]
      Description=Refresh Proton VPN NAT-PMP port and update qBittorrent
      After=qbittorrent.service wg-quick@{{ proton_wg_interface }}.service
      Requires=qbittorrent.service wg-quick@{{ proton_wg_interface }}.service

      [Service]
      Type=oneshot
      ExecStart=/usr/local/bin/proton-natpmp-qbt --gateway {{ proton_natpmp_gateway }} --qbt-url http://127.0.0.1:{{ qbittorrent_webui_port }} --qbt-password {{ qbittorrent_webui_password }}

- name: Install NAT-PMP timer
  ansible.builtin.copy:
    dest: /etc/systemd/system/proton-natpmp-qbt.timer
    mode: "0644"
    content: |
      [Unit]
      Description=Run Proton NAT-PMP qBittorrent updater every 45 seconds

      [Timer]
      OnBootSec=30
      OnUnitActiveSec=45
      AccuracySec=1

      [Install]
      WantedBy=timers.target
  no_log: true

- name: Install qBittorrent service
  ansible.builtin.template:
    src: qbittorrent.service.j2
    dest: /etc/systemd/system/qbittorrent.service
    mode: "0644"

- name: Enable qBittorrent and NAT-PMP timer
  ansible.builtin.systemd:
    name: "{{ item }}"
    daemon_reload: true
    enabled: true
    state: started
  loop:
    - qbittorrent.service
    - proton-natpmp-qbt.timer
```

- [ ] **Step 13: Create qBittorrent backup script**

Create `scripts/migration/backup-qbittorrent.sh`:

```sh
#!/bin/sh
set -eu

OLD_HOST="${OLD_DOCKER_HOST}"
BACKUP_DIR="${BACKUP_DIR:-./migration-backups/qbittorrent}"

mkdir -p "${BACKUP_DIR}"

ssh "${OLD_HOST}" "docker cp qbittorrent:/config - | gzip -c" > "${BACKUP_DIR}/container-config.tar.gz"
ssh "${OLD_HOST}" "docker inspect qbittorrent" > "${BACKUP_DIR}/docker-inspect.json"

echo "qBittorrent backup written to ${BACKUP_DIR}"
```

- [ ] **Step 14: Add downloads roles to site playbook**

Modify `infra/ansible/playbooks/site.yml` so it includes:

```yaml
- name: Configure downloads LXC
  hosts: downloads
  gather_facts: true
  roles:
    - downloads_vpn
    - qbittorrent
```

- [ ] **Step 15: Run tests and syntax check**

Run:

```powershell
python -m pytest tests/downloads/test_proton_natpmp_qbt.py -q
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
```

Expected:

- Python test reports `2 passed`.
- Ansible syntax check reports `playbook:`.

- [ ] **Step 16: Commit**

```powershell
git add requirements-dev.txt apps/downloads tests/downloads infra/ansible scripts/migration
git commit -m "feat: add native downloads vpn and qbittorrent roles"
```

Expected: commit succeeds.

## Task 8: Files LXC With Copyparty

**Files:**
- Create: `apps/files/copyparty.conf`
- Create: `infra/ansible/inventory/prod/group_vars/files.yml`
- Create: `infra/ansible/roles/copyparty/tasks/main.yml`
- Create: `infra/ansible/roles/copyparty/templates/copyparty.openrc.j2`
- Create: `scripts/migration/backup-copyparty.sh`
- Modify: `infra/ansible/playbooks/site.yml`

- [ ] **Step 1: Create files variables**

Create `infra/ansible/inventory/prod/group_vars/files.yml`:

```yaml
copyparty_listen_port: 3923
copyparty_config_path: /etc/copyparty/copyparty.conf
copyparty_state_dir: /var/lib/copyparty
copyparty_downloads_path: /srv/downloads
```

- [ ] **Step 2: Create Copyparty config**

Create `apps/files/copyparty.conf`:

```ini
[global]
  e2dsa
  e2ts
  ansi
  rproxy: -1
  xff-src: 192.168.0.10
  name: copyparty.hchu.me
  localtime
  gsel
  lang: kor
  grid
  p: 3923

[/public]
  /srv/public
  accs:
    r: *
    A: holybaechu

[/shared-readonly]
  /srv/shared-readonly
  accs:
    r: siregon72, ezmin1104, sieon, emuyoz, ys100503
    A: holybaechu

[/downloads]
  /srv/downloads
  accs:
    r: holybaechu
    A: holybaechu
```

Do not copy the mojibake paths from the old file into this new config. Add verified UTF-8 Korean contest paths in a separate commit after confirming the intended names.

- [ ] **Step 3: Create Copyparty OpenRC template**

Create `infra/ansible/roles/copyparty/templates/copyparty.openrc.j2`:

```sh
#!/sbin/openrc-run

name="copyparty"
description="Copyparty file server"
command="/opt/copyparty/bin/copyparty"
command_args="-c {{ copyparty_config_path }}"
command_background="yes"
pidfile="/run/copyparty.pid"
command_user="{{ service_user }}:{{ service_group }}"
output_log="/var/log/copyparty.log"
error_log="/var/log/copyparty.err"

depend() {
	need net
}
```

- [ ] **Step 4: Create Copyparty role**

Create `infra/ansible/roles/copyparty/tasks/main.yml`:

```yaml
---
- name: Install Copyparty dependencies
  community.general.apk:
    name:
      - python3
      - py3-pip
      - py3-virtualenv
      - ffmpeg
    update_cache: true
    state: present

- name: Create Copyparty directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0750"
  loop:
    - /etc/copyparty
    - "{{ copyparty_state_dir }}"
    - /srv/public
    - /srv/shared-readonly
    - "{{ copyparty_downloads_path }}"

- name: Create Copyparty virtualenv
  ansible.builtin.command:
    cmd: python3 -m venv /opt/copyparty
    creates: /opt/copyparty/bin/python

- name: Install Copyparty
  ansible.builtin.pip:
    name: copyparty==1.20.16
    virtualenv: /opt/copyparty
    state: present

- name: Install Copyparty config
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../../../apps/files/copyparty.conf"
    dest: "{{ copyparty_config_path }}"
    owner: "{{ service_user }}"
    group: "{{ service_group }}"
    mode: "0640"

- name: Install Copyparty OpenRC service
  ansible.builtin.template:
    src: copyparty.openrc.j2
    dest: /etc/init.d/copyparty
    mode: "0755"

- name: Enable Copyparty
  ansible.builtin.service:
    name: copyparty
    enabled: true
    state: started
```

- [ ] **Step 5: Create Copyparty backup script**

Create `scripts/migration/backup-copyparty.sh`:

```sh
#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-./migration-backups/copyparty}"
mkdir -p "${BACKUP_DIR}"

cp stacks/copyparty/copyparty.conf "${BACKUP_DIR}/copyparty.conf"
cp stacks/copyparty/compose.yaml "${BACKUP_DIR}/compose.yaml"

echo "Copyparty config backup written to ${BACKUP_DIR}"
echo "Verify the old Korean path names before migrating contest shares."
```

- [ ] **Step 6: Add Copyparty role to site playbook**

Modify `infra/ansible/playbooks/site.yml` so it includes:

```yaml
- name: Configure files LXC
  hosts: files
  gather_facts: true
  roles:
    - copyparty
```

- [ ] **Step 7: Syntax check**

Run:

```powershell
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
```

Expected: syntax check reports `playbook:`.

- [ ] **Step 8: Commit**

```powershell
git add apps/files infra/ansible scripts/migration
git commit -m "feat: add copyparty files role"
```

Expected: commit succeeds.

## Task 9: Validation Playbook

**Files:**
- Create: `infra/ansible/playbooks/validate.yml`

- [ ] **Step 1: Create validation playbook**

Create `infra/ansible/playbooks/validate.yml`:

```yaml
---
- name: Validate edge
  hosts: edge
  gather_facts: false
  tasks:
    - name: Validate Caddy config
      ansible.builtin.command:
        cmd: caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
      changed_when: false

    - name: Check Caddy service
      ansible.builtin.command:
        cmd: rc-service caddy status
      changed_when: false

- name: Validate DNS
  hosts: dns
  gather_facts: false
  tasks:
    - name: Check AdGuard service
      ansible.builtin.command:
        cmd: rc-service adguardhome status
      changed_when: false

    - name: Query AdGuard normal DNS
      ansible.builtin.command:
        cmd: drill @127.0.0.1 example.com
      changed_when: false

- name: Validate tailnet
  hosts: tailnet
  gather_facts: false
  tasks:
    - name: Check tailscaled service
      ansible.builtin.command:
        cmd: systemctl is-active tailscaled
      changed_when: false

    - name: Check Tailscale status
      ansible.builtin.command:
        cmd: tailscale status
      changed_when: false

- name: Validate downloads
  hosts: downloads
  gather_facts: false
  tasks:
    - name: Check WireGuard service
      ansible.builtin.command:
        cmd: systemctl is-active wg-quick@wg-proton
      changed_when: false

    - name: Check qBittorrent service
      ansible.builtin.command:
        cmd: systemctl is-active qbittorrent
      changed_when: false

    - name: Check qBittorrent Web UI port
      ansible.builtin.wait_for:
        host: 127.0.0.1
        port: 8080
        timeout: 5

    - name: Check Proton NAT-PMP timer
      ansible.builtin.command:
        cmd: systemctl is-active proton-natpmp-qbt.timer
      changed_when: false

- name: Validate files
  hosts: files
  gather_facts: false
  tasks:
    - name: Check Copyparty service
      ansible.builtin.command:
        cmd: rc-service copyparty status
      changed_when: false

    - name: Check Copyparty port
      ansible.builtin.wait_for:
        host: 127.0.0.1
        port: 3923
        timeout: 5
```

- [ ] **Step 2: Syntax check**

Run:

```powershell
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml --syntax-check
```

Expected: syntax check reports `playbook:`.

- [ ] **Step 3: Commit**

```powershell
git add infra/ansible/playbooks/validate.yml
git commit -m "test: add homelab validation playbook"
```

Expected: commit succeeds.

## Task 10: GitHub Actions CD And CI Scripts

**Files:**
- Create: `.github/workflows/cd.yml`
- Create: `scripts/ci/install-tools.sh`
- Create: `scripts/ci/configure-ssh.sh`
- Create: `scripts/ci/tofu-plan.sh`
- Create: `scripts/ci/tofu-apply.sh`

- [ ] **Step 1: Create install script**

Create `scripts/ci/install-tools.sh`:

```sh
#!/bin/sh
set -eu

sudo apt-get update
sudo apt-get install -y curl unzip python3-pip openssh-client
python3 -m pip install --user ansible

TOFU_VERSION="1.9.0"
curl -fsSLo /tmp/tofu.zip "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_amd64.zip"
sudo unzip -o /tmp/tofu.zip tofu -d /usr/local/bin
tofu version
ansible --version
```

- [ ] **Step 2: Create SSH config script**

Create `scripts/ci/configure-ssh.sh`:

```sh
#!/bin/sh
set -eu

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
printf '%s\n' "${DEPLOY_SSH_PRIVATE_KEY}" > "${HOME}/.ssh/id_ed25519"
chmod 600 "${HOME}/.ssh/id_ed25519"

ssh-keyscan -H "${PVE_TAILSCALE_IP}" >> "${HOME}/.ssh/known_hosts"
ssh-keyscan -H 192.168.0.10 192.168.0.11 192.168.0.12 192.168.0.13 192.168.0.14 >> "${HOME}/.ssh/known_hosts"
```

- [ ] **Step 3: Create OpenTofu plan script**

Create `scripts/ci/tofu-plan.sh`:

```sh
#!/bin/sh
set -eu

cd infra/opentofu/envs/prod
tofu init
tofu fmt -recursive -check ../..
tofu validate
tofu plan -out=prod.tfplan
```

- [ ] **Step 4: Create OpenTofu apply script**

Create `scripts/ci/tofu-apply.sh`:

```sh
#!/bin/sh
set -eu

cd infra/opentofu/envs/prod
tofu apply -auto-approve prod.tfplan
```

- [ ] **Step 5: Create workflow**

Create `.github/workflows/cd.yml`:

```yaml
name: cd

on:
  workflow_dispatch:
  push:
    branches:
      - main

permissions:
  contents: read
  id-token: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: prod
    env:
      TF_VAR_proxmox_endpoint: ${{ secrets.PROXMOX_ENDPOINT }}
      TF_VAR_proxmox_api_token: ${{ secrets.PROXMOX_API_TOKEN }}
      TF_VAR_proxmox_insecure_tls: "true"
      DEPLOY_SSH_PRIVATE_KEY: ${{ secrets.DEPLOY_SSH_PRIVATE_KEY }}
      PVE_TAILSCALE_IP: ${{ vars.PVE_TAILSCALE_IP }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Connect Tailscale
        uses: tailscale/github-action@v4
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          audience: ${{ secrets.TS_AUDIENCE }}
          tags: tag:ci
          version: latest
          ping: ${{ vars.PVE_TAILSCALE_IP }}

      - name: Install tooling
        run: ./scripts/ci/install-tools.sh

      - name: Configure SSH
        run: ./scripts/ci/configure-ssh.sh

      - name: OpenTofu plan
        run: ./scripts/ci/tofu-plan.sh

      - name: OpenTofu apply
        if: github.ref == 'refs/heads/main'
        run: ./scripts/ci/tofu-apply.sh

      - name: Install Ansible collections
        run: ansible-galaxy collection install -r infra/ansible/requirements.yml

      - name: Deploy services
        run: ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml

      - name: Validate services
        run: ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml

      - name: Disconnect Tailscale
        if: always()
        run: sudo tailscale logout || true
```

- [ ] **Step 6: Mark scripts executable in Git**

Run:

```powershell
git update-index --chmod=+x scripts/ci/install-tools.sh scripts/ci/configure-ssh.sh scripts/ci/tofu-plan.sh scripts/ci/tofu-apply.sh
```

Expected: command exits with code `0`.

- [ ] **Step 7: Commit**

```powershell
git add .github/workflows/cd.yml scripts/ci
git commit -m "ci: add tailscale opentofu ansible deployment"
```

Expected: commit succeeds.

## Task 11: Legacy Stack Archive And Active Scope Cleanup

**Files:**
- Move: `stacks/traefik` to `legacy/docker-stacks/traefik`
- Move: `stacks/adguard` to `legacy/docker-stacks/adguard`
- Move: `stacks/tailscale` to `legacy/docker-stacks/tailscale`
- Move: `stacks/qbittorrent` to `legacy/docker-stacks/qbittorrent`
- Move: `stacks/copyparty` to `legacy/docker-stacks/copyparty`
- Move: `stacks/ddns` to `legacy/docker-stacks/ddns`
- Move: `core/compose.yaml` to `legacy/docker-stacks/komodo/compose.yaml`
- Move: `stacks/navidrome` to `legacy/docker-stacks/navidrome`
- Move: `stacks/openwebui` to `legacy/docker-stacks/openwebui`
- Move: `stacks/minecraft` to `legacy/docker-stacks/minecraft`
- Create: `legacy/README.md`

- [ ] **Step 1: Confirm validation before archive**

Run:

```powershell
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml
```

Expected: all validation plays complete with `failed=0`.

- [ ] **Step 2: Move preserved legacy stacks**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'legacy/docker-stacks' | Out-Null
Move-Item -LiteralPath 'stacks/traefik' -Destination 'legacy/docker-stacks/traefik'
Move-Item -LiteralPath 'stacks/adguard' -Destination 'legacy/docker-stacks/adguard'
Move-Item -LiteralPath 'stacks/tailscale' -Destination 'legacy/docker-stacks/tailscale'
Move-Item -LiteralPath 'stacks/qbittorrent' -Destination 'legacy/docker-stacks/qbittorrent'
Move-Item -LiteralPath 'stacks/copyparty' -Destination 'legacy/docker-stacks/copyparty'
Move-Item -LiteralPath 'stacks/ddns' -Destination 'legacy/docker-stacks/ddns'
```

Expected: preserved stack directories now exist under `legacy/docker-stacks`.

- [ ] **Step 3: Move deprecated stack files**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'legacy/docker-stacks/komodo' | Out-Null
Move-Item -LiteralPath 'core/compose.yaml' -Destination 'legacy/docker-stacks/komodo/compose.yaml'
Move-Item -LiteralPath 'stacks/navidrome' -Destination 'legacy/docker-stacks/navidrome'
Move-Item -LiteralPath 'stacks/openwebui' -Destination 'legacy/docker-stacks/openwebui'
Move-Item -LiteralPath 'stacks/minecraft' -Destination 'legacy/docker-stacks/minecraft'
```

Expected: deprecated services are archived under `legacy/docker-stacks`.

- [ ] **Step 4: Create legacy README**

Create `legacy/README.md`:

```markdown
# Legacy Docker Stacks

This directory preserves the old Docker Compose stack definitions for audit and rollback reference.

Active deployment has moved to:

- OpenTofu: `infra/opentofu`
- Ansible: `infra/ansible`
- App configuration: `apps`
- GitHub Actions CD: `.github/workflows/cd.yml`

Only these old stacks were preserved as migration source material:

- `traefik`
- `adguard`
- `tailscale`
- `qbittorrent`
- `copyparty`
- `ddns`

The old Komodo, Navidrome, OpenWebUI, and Minecraft stacks are archived as deprecated services.
```

- [ ] **Step 5: Verify no active Compose files remain**

Run:

```powershell
rg --files | Select-String -Pattern '(^|/)compose\.yaml$'
```

Expected: only paths under `legacy/docker-stacks` are printed.

- [ ] **Step 6: Commit**

```powershell
git add -A legacy stacks core
git commit -m "chore: archive legacy docker stacks"
```

Expected: commit succeeds.

## Task 12: Full Verification And Cutover Runbook

**Files:**
- Create: `docs/runbooks/proxmox-lxc-cutover.md`

- [ ] **Step 1: Create runbook directory**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'docs/runbooks' | Out-Null
```

Expected: command exits with code `0`.

- [ ] **Step 2: Create cutover runbook**

Create `docs/runbooks/proxmox-lxc-cutover.md`:

```markdown
# Proxmox LXC Cutover Runbook

## Pre-Cutover

1. Confirm OpenTofu plan is clean.
2. Confirm Ansible site playbook completes with no failures.
3. Confirm validation playbook completes with no failures.
4. Back up old Docker service data:
   - `scripts/migration/backup-adguard.sh`
   - `scripts/migration/backup-qbittorrent.sh`
   - `scripts/migration/backup-copyparty.sh`
5. Snapshot the five new LXCs in Proxmox.
6. Snapshot or back up the old Docker host.

## DNS Cutover

1. Set router DHCP DNS server to `192.168.0.11`.
2. Confirm `dig @192.168.0.11 hchu.me` works from a LAN client.
3. Confirm `adguard.home.hchu.me` resolves to `192.168.0.10`.

## Edge Cutover

1. Forward TCP `80` and `443` on the router to `192.168.0.10`.
2. Open `https://copyparty.hchu.me`.
3. Open `https://qbt.home.hchu.me` from LAN or Tailscale and confirm non-private clients receive HTTP `403`.

## Encrypted DNS Cutover

1. Forward TCP `853` to `192.168.0.11` only if public DoT is required.
2. Forward UDP `853` to `192.168.0.11` only if public DoQ is required.
3. Confirm AdGuard has a valid `dns.hchu.me` certificate.

## Downloads Cutover

1. Confirm `/downloads/incomplete` is not mounted into the `files` LXC.
2. Confirm `/downloads/complete` is mounted read-only in the `files` LXC at `/srv/downloads`.
3. Confirm qBittorrent external IP is a Proton VPN IP.
4. Confirm NAT-PMP updater sets a non-zero qBittorrent listen port.

## Rollback

1. Restore router port forwards to the old Docker edge.
2. Restore router DHCP DNS to the previous DNS server.
3. Stop affected new LXCs.
4. Restore Proxmox snapshots if service state was changed.
5. Re-run validation after restoring the previous Git SHA.
```

- [ ] **Step 3: Run final repository validation**

Run:

```powershell
tofu fmt -recursive infra/opentofu
python -m pytest tests/downloads/test_proton_natpmp_qbt.py -q
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml --syntax-check
```

Expected:

- OpenTofu format exits `0`.
- Python tests report `2 passed`.
- Both Ansible syntax checks report `playbook:`.

- [ ] **Step 4: Commit**

```powershell
git add docs/runbooks
git commit -m "docs: add lxc cutover runbook"
```

Expected: commit succeeds.

## Spec Coverage Map

| Spec area | Plan task |
|---|---|
| Final LXC layout and OS choice | Task 2 |
| Native packages, binaries, OpenRC/systemd services | Tasks 3 through 8 |
| VM vs LXC choice | Captured in design; Task 7 keeps `downloads` as LXC with VM escape hatch outside baseline |
| Caddy replacing Traefik | Task 4 |
| AdGuard web UI, DoH, DoT | Tasks 4 and 5 |
| AdGuard dedicated ACME | Task 5 |
| OpenTofu module structure | Task 2 |
| Ansible Alpine/Debian structure | Task 3 |
| GitHub Actions with Tailscale | Task 10 |
| Validation, health checks, rollback | Tasks 9 and 12 |
| Network plan | Tasks 2, 4, 5, 6, and 12 |
| Storage and permissions | Tasks 2, 7, 8, and 12 |
| Security risks and mitigations | Tasks 4 through 10 and 12 |
| Final repository structure | Task 1 |
| Migration from current stacks | Tasks 5, 7, 8, 11, and 12 |
| Files to remove, rename, split, merge, archive, or ignore | Task 11 |
| Preserved vs intentionally dropped services | Task 11 |
