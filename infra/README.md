# Infrastructure

`opentofu` defines the Proxmox LXC shape that can be managed through the Proxmox API token: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and base feature flags.

`ansible` configures Tailscale directly on the tailnet appliance and Docker
Engine plus every Compose project on the application LXC. It also applies
root-only Proxmox settings such as `/dev/net/tun`, nesting, and the shared data
bind mount.

The desired topology is exactly two LXCs. Retired per-service containers are
forgotten from state without destruction during cutover and remain manual
rollback targets until explicitly retired.

OpenTofu state is not committed to Git.
