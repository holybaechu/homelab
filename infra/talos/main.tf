terraform {
  required_version = "= 1.13.2"
  required_providers {
    talos = {
      source  = "siderolabs/talos"
      version = "0.12.0"
    }
  }
  backend "local" {}
  encryption {
    # Keep the original key provider name so existing encrypted state stays readable.
    key_provider "pbkdf2" "homelab" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "homelab" {
      keys = key_provider.pbkdf2.homelab
    }
    state {
      method   = method.aes_gcm.homelab
      enforced = true
    }
    plan {
      method   = method.aes_gcm.homelab
      enforced = true
    }
  }
}

variable "state_passphrase" {
  type      = string
  sensitive = true
  ephemeral = true
  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "Use the existing state passphrase from 1Password, at least 16 characters."
  }
}

variable "confirm_disk_serial" {
  type        = string
  default     = ""
  description = "Exact installation disk serial acknowledged when planning a node change."
}

variable "bootstrap" {
  type    = bool
  default = false
}

locals {
  settings           = yamldecode(file("${path.module}/../../kubernetes/clusters/homelab/settings.yaml")).data
  talos_version      = local.settings.TALOS_VERSION
  kubernetes_version = local.settings.KUBERNETES_VERSION
  installer_image    = "factory.talos.dev/metal-installer/376567988ad370138ad8b2698212367b8edcb69b5fd68c80be1f2ec7d603b4ba:${local.talos_version}"
  disk_match         = "'${local.settings.INSTALL_DISK_ID}' in disk.symlinks && disk.transport == 'sata'"
  networks           = [local.settings.LAN_CIDR, local.settings.POD_CIDR, local.settings.SERVICE_CIDR]
  private_sources = [
    { subnet = local.settings.LAN_CIDR },
    { subnet = "100.64.0.0/10" },
  ]
  pod_sources = concat(local.private_sources, [{ subnet = local.settings.POD_CIDR }])
  firewall_rules = [
    { name = "dns-tcp", ports = [53], protocol = "tcp", sources = local.pod_sources },
    { name = "dns-udp", ports = [53], protocol = "udp", sources = local.pod_sources },
    { name = "talos-api", ports = [50000], protocol = "tcp", sources = local.private_sources },
    { name = "kubernetes-api", ports = [6443], protocol = "tcp", sources = local.pod_sources },
    { name = "adguard-http", ports = [3000], protocol = "tcp", sources = local.pod_sources },
    { name = "kubelet", ports = [10250], protocol = "tcp", sources = local.pod_sources },
  ]
  config_patches = concat([
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "UnattendedInstallConfig"
      installer  = { image = local.installer_image }
      provisioning = {
        diskSelector = { match = local.disk_match }
        wipe         = true
      }
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "LinkAliasConfig"
      name       = "lan"
      selector   = { match = "mac(link.permanent_addr) == '${local.settings.NODE_MAC}'" }
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "LinkConfig"
      name       = "lan"
      addresses  = [{ address = local.settings.NODE_CIDR }]
      routes     = [{ gateway = local.settings.GATEWAY_IP }]
    }),
    yamlencode({
      apiVersion  = "v1alpha1"
      kind        = "ResolverConfig"
      nameservers = [{ address = "1.1.1.1" }, { address = "1.0.0.1" }]
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "HostnameConfig"
      "$patch"   = "delete"
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "HostnameConfig"
      hostname   = "talos-${local.settings.CLUSTER_NAME}"
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "SysctlConfig"
      params = {
        "net.ipv4.ip_forward"          = "1"
        "net.ipv6.conf.all.forwarding" = "1"
      }
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "KubeNodeConfig"
      "$patch"   = "delete"
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "KubeNodeConfig"
      nodeIP     = { validSubnets = [local.settings.LAN_CIDR] }
      labels     = { "node-role.kubernetes.io/control-plane" = "" }
    }),
    yamlencode({
      apiVersion     = "v1alpha1"
      kind           = "KubeNetworkConfig"
      podSubnets     = [local.settings.POD_CIDR]
      serviceSubnets = [local.settings.SERVICE_CIDR]
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "VolumeConfig"
      name       = "EPHEMERAL"
      provisioning = {
        diskSelector = { match = local.disk_match }
        minSize      = "100GiB"
        maxSize      = "100GiB"
      }
    }),
    yamlencode({
      apiVersion = "v1alpha1"
      kind       = "UserVolumeConfig"
      name       = "homelab"
      volumeType = "partition"
      provisioning = {
        diskSelector = { match = local.disk_match }
        minSize      = "100GiB"
        grow         = true
      }
      filesystem = { type = "xfs" }
    }),
    yamlencode({ apiVersion = "v1alpha1", kind = "NetworkDefaultActionConfig", ingress = "accept" }),
    ], [for rule in local.firewall_rules : yamlencode({
      apiVersion   = "v1alpha1"
      kind         = "NetworkRuleConfig"
      name         = rule.name
      portSelector = { ports = rule.ports, protocol = rule.protocol }
      ingress      = rule.sources
  })])
}

resource "talos_machine_secrets" "cluster" {
  talos_version = local.talos_version
  lifecycle { prevent_destroy = true }
}

data "talos_machine_configuration" "node" {
  cluster_name       = local.settings.CLUSTER_NAME
  cluster_endpoint   = "https://${local.settings.NODE_IP}:6443"
  machine_type       = "controlplane"
  machine_secrets    = talos_machine_secrets.cluster.machine_secrets
  talos_version      = local.talos_version
  kubernetes_version = local.kubernetes_version
  config_patches     = local.config_patches
  lifecycle {
    precondition {
      condition = (
        can(regex("^[A-Za-z0-9]+$", local.settings.INSTALL_DISK_SERIAL)) &&
        startswith(local.settings.INSTALL_DISK_ID, "/dev/disk/by-id/ata-") &&
        endswith(local.settings.INSTALL_DISK_ID, "_${local.settings.INSTALL_DISK_SERIAL}") &&
        startswith(local.settings.PROTECTED_USB_ID, "/dev/disk/by-id/usb-")
      )
      error_message = "Select the exact internal SATA serial and a separate protected USB identity."
    }
    precondition {
      condition = try(
        split("/", local.settings.NODE_CIDR)[0] == local.settings.NODE_IP &&
        cidrcontains(local.settings.LAN_CIDR, local.settings.NODE_CIDR) &&
        cidrcontains(local.settings.LAN_CIDR, local.settings.GATEWAY_IP), false
      )
      error_message = "Node address, prefix and gateway must belong to the declared LAN."
    }
    precondition {
      condition = try(alltrue(flatten([
        for i, left in local.networks : [
          for j, right in local.networks :
          !cidrcontains(left, cidrhost(right, 0)) && !cidrcontains(right, cidrhost(left, 0)) if i < j
        ]
      ])), false)
      error_message = "LAN, Pod and Service networks must not overlap."
    }
    precondition {
      condition = (
        local.settings.DATA_PATH == "/var/mnt/homelab" &&
        can(regex("^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$", local.settings.NODE_MAC))
      )
      error_message = "Keep the Talos homelab volume path and specify an exact node MAC address."
    }
  }
}

data "talos_client_configuration" "cluster" {
  cluster_name         = local.settings.CLUSTER_NAME
  client_configuration = talos_machine_secrets.cluster.client_configuration
  endpoints            = [local.settings.NODE_IP]
  nodes                = [local.settings.NODE_IP]
}

resource "talos_machine_configuration_apply" "node" {
  node                        = local.settings.NODE_IP
  endpoint                    = local.settings.NODE_IP
  client_configuration        = talos_machine_secrets.cluster.client_configuration
  machine_configuration_input = data.talos_machine_configuration.node.machine_configuration
  apply_mode                  = "auto"
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = var.confirm_disk_serial == local.settings.INSTALL_DISK_SERIAL
      error_message = "Run scripts/talos-preflight, then acknowledge the exact disk with -var=confirm_disk_serial=SERIAL."
    }
  }
}

resource "talos_machine_bootstrap" "cluster" {
  count                = var.bootstrap ? 1 : 0
  depends_on           = [talos_machine_configuration_apply.node]
  node                 = local.settings.NODE_IP
  endpoint             = local.settings.NODE_IP
  client_configuration = talos_machine_secrets.cluster.client_configuration
  lifecycle { prevent_destroy = true }
}

resource "talos_cluster_kubeconfig" "cluster" {
  count                = var.bootstrap ? 1 : 0
  depends_on           = [talos_machine_bootstrap.cluster]
  node                 = local.settings.NODE_IP
  endpoint             = local.settings.NODE_IP
  client_configuration = talos_machine_secrets.cluster.client_configuration
}

output "talosconfig" {
  value     = data.talos_client_configuration.cluster.talos_config
  sensitive = true
}

output "machine_configuration" {
  value     = data.talos_machine_configuration.node.machine_configuration
  sensitive = true
}

output "kubeconfig" {
  value     = var.bootstrap ? talos_cluster_kubeconfig.cluster[0].kubeconfig_raw : ""
  sensitive = true
}
