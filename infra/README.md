# Infrastructure

`opentofu` defines the Proxmox LXC shape: VMIDs, OS templates, static IPs, CPU, memory, disks, tags, startup order, and optional mount points.

`ansible` configures packages, services, templates, validation checks, and host-level bind mounts.

OpenTofu state is not committed to Git.
