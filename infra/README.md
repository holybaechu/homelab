# Infrastructure

`opentofu` defines the Proxmox LXC shape that can be managed through the Proxmox API token: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and base feature flags.

`ansible` configures Tailscale directly on the tailnet appliance, Docker Engine
and the workload Compose projects on the application LXC, the active native
OpenClaw Gateway plus its local CTF Docker Engine on one dedicated LXC, and the
separate Arcane management control plane. It also applies root-only Proxmox
settings. VMID 111 retains `/dev/net/tun` for Tailscale. VMID 110 has only the
Docker nesting/keyctl settings and shared-data bind mount. Dedicated OpenClaw
VMID 118 is unprivileged, has nesting/keyctl for its local Docker daemon, and
has no TUN passthrough. CTF sandboxes are session-scoped containers on that
local daemon; they receive only the CTF workspace, persistent package/browser
caches, and generated sandbox skills selected by OpenClaw.
The retired Hermes data at `/srv/homelab/hermes` is preserved but unmanaged.
The retired VPN qBittorrent data at
`/srv/homelab/docker-apps/qbittorrent-vpn` is likewise preserved but unmanaged
for recovery.

Arcane may perform fast day-to-day workload operations, but it does not own its
own installation or the LXC. OpenTofu and Ansible remain the authoritative
bootstrap and break-glass recovery path.

VMID 118 owns the active native OpenClaw Gateway, local Kali sandbox image,
firewall, and private `openclaw-setup` checkout. Runtime state, CTF evidence, and all
credentials remain outside Git. The exact former Docker Gateway is retained
stopped on VMID 110 behind a permanent source hold for explicit tracked rollback
only.

The desired topology is exactly three LXCs. Legacy per-service containers are
forgotten from state without destruction during cutover and remain stopped; only
services not explicitly retired may be used as manual rollback targets.

OpenTofu state is not committed to Git.

Renovate updates versions stored in Git; Ansible upgrades apt-owned dependencies
during deployment. Arcane Manager and its Docker socket proxy are always
review-gated and do not inherit the general minor/patch automerge policy.
