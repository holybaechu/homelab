# Infrastructure

`opentofu` defines the Proxmox LXC shape that can be managed through the Proxmox API token: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and base feature flags.

`ansible` configures packages, services, templates, validation checks, and root-only Proxmox LXC settings such as bind mounts and non-nesting feature flags.

OpenTofu state is not committed to Git.
