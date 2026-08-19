# Infrastructure

`opentofu` defines the Proxmox LXC shape that can be managed through the Proxmox API token: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and base feature flags.

`ansible/inventory/prod/topology.json` is the single production topology: Ansible
loads it as static inventory and OpenTofu decodes the same JSON directly.

`ansible` configures Tailscale directly on the tailnet appliance, Docker Engine
and private runtime inputs for the single `homelab` Compose project on the
application LXC, plus the immutable OpenClaw Compose runtime and its local CTF
Docker Engine on one dedicated LXC.
It also applies root-only Proxmox
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

App-only releases copy a validated bundle to the application LXC. The locked
direct deployer exclusively activates or rolls back the `homelab` project;
Ansible does not run Compose lifecycle commands. OpenTofu and Ansible remain
the authoritative infrastructure bootstrap and runtime-input preparation path.

VMID 118 owns the exact-digest OpenClaw Gateway and CTF images, firewall, and
mutable runtime state. CI bundles an exact private `openclaw-setup` commit;
the LXC does not retain a production Git checkout or assemble images. Runtime
state, CTF evidence, and credentials remain outside Git. The retired Gateway
is represented only by its protected offline recovery manifest and OCI/config
artifacts, not an always-on rollback stack.

The desired topology is exactly three LXCs. Legacy per-service containers are
forgotten from state without destruction during cutover and remain stopped; only
services not explicitly retired may be used as manual rollback targets.

OpenTofu state is not committed to Git.

Renovate updates versions stored in Git; scheduled maintenance upgrades
apt-owned dependencies. OpenClaw image updates remain review-gated and
do not inherit the general minor/patch automerge policy.
