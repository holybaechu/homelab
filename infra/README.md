# Infrastructure

`opentofu` defines the Proxmox LXC shape that can be managed through the Proxmox API token: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and base feature flags.

`ansible` configures Tailscale directly on the tailnet appliance, Docker Engine
and the two workload Compose projects (`platform` and `media`) on the
application LXC, and the separate Arcane management control plane. It also
applies root-only Proxmox settings. VMID 111 retains `/dev/net/tun` for
Tailscale. VMID 110 has no TUN passthrough; it retains only the Docker
nesting/keyctl settings and shared data bind mount required by the application
stack.
The retired Hermes data at `/srv/homelab/hermes` is preserved but unmanaged.
The retired VPN qBittorrent data at
`/srv/homelab/docker-apps/qbittorrent-vpn` is likewise preserved but unmanaged
for recovery.

Arcane may perform fast day-to-day workload operations, but it does not own its
own installation or the LXC. OpenTofu and Ansible remain the authoritative
bootstrap and break-glass recovery path.

The desired topology is exactly two LXCs. Legacy per-service containers are
forgotten from state without destruction during cutover and remain stopped;
only services not explicitly retired may be used as manual rollback targets.

OpenTofu state is not committed to Git.

Renovate updates versions stored in Git; Ansible upgrades apt-owned dependencies
during deployment. Arcane Manager and its Docker socket proxy are always
review-gated and do not inherit the general minor/patch automerge policy.
