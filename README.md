# holybaechu/homelab

A Kubernetes homelab setup using Talos Linux + ArgoCD optimized for single-node clusters

## Architecture Overview

The homelab consists of the following components:

### Core Infrastructure
- **ArgoCD**: GitOps continuous deployment tool
- **MetalLB**: Load balancer for bare metal clusters (IP Range is 192.168.0.200-192.168.0.250)
- **Rook-Ceph**: Distributed storage system
- **Cert-Manager**: SSL certificate management with Cloudflare DNS challenge
- **Ingress-Nginx**: Kubernetes ingress controller (Assigned IP is 192.168.0.200)

### Applications
- **qBittorrent**: BitTorrent client (Assigned IP is 192.168.0.201) (BitTorrent port is 696969)
### Ingresses to outside services
- **Router**: Network routing service (Routed IP is 192.168.0.1)
- **Proxmox**: Virtualization management interface (Routed IP is 192.168.0.2)
- **AdGuard Home**: DNS ad-blocker and network-wide content filtering (Routed IP is 192.168.0.4)

## Prerequisites

Before setting up the homelab, ensure you have:

- **1Password CLI** installed and configured
- **A Talos Linux node** running with network connectivity
- **An 1Password separate vault** for storing Cloudflare API Token
- **A Cloudflare API Token** stored in 1Password with name "Cloudflare API Token" with type "API Credentials"
- **kubectl** configured to access your Talos cluster
- **talosctl** configured with your cluster
- **helm** installed

## Setup Instructions

### 1. Apply Talos Linux Patch

Apply the required Talos Linux configuration patch:

```bash
talosctl patch mc --nodes $YOUR_NODE_IPS --talosconfig=./talosconfig --patch @talos/00-patch.yaml
```

### 2. Install 1Password Operator

Set up the 1Password Connect operator for secret management:

```bash
# Create 1Password Connect server
op connect server create $YOUR_SERVER_NAME --vaults $YOUR_VAULT_NAME

# Install the operator using Helm
helm install connect 1password/connect \
  --set-file connect.credentials=1password-credentials.json \
  --set operator.create=true \
  --set operator.token.value=$(op connect token create onepassword --server $YOUR_SERVER_NAME --vault $YOUR_VAULT_NAME)
```

### 3. Deploy Bootstrap Configuration

Apply the bootstrap configuration which installs ArgoCD and the root application:

```bash
kubectl apply -k bootstrap
```

### 4. Access ArgoCD

After the bootstrap deployment completes:

1. **Get ArgoCD admin password**:
   ```bash
   argocd admin initial-password -n argocd
   ```

2. **Port forward to ArgoCD UI**:
   ```bash
   kubectl port-forward svc/argocd-server -n argocd 8080:443
   ```

3. **Access ArgoCD UI** at `https://localhost:8080`
   - Username: `admin`
   - Password: (from step 1)

### 5. Monitor Deployments

Once ArgoCD is running, it will automatically deploy all applications defined in the `apps/` directory. Monitor the deployment status in the ArgoCD UI.

## Upgrading Talos Linux / Kubernetes
To upgrade Talos Linux to the latest version, run the following command:
```bash
talosctl upgrade --nodes $YOUR_NODE_IPS --talosconfig=./talosconfig --image ghcr.io/siderolabs/installer:latest
```
To upgrade Kubernetes to the latest version, run the following command:
```bash
talosctl upgrade-k8s --nodes $YOUR_NODE_IPS --talosconfig=./talosconfig
```