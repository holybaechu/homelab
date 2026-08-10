# Arcane control plane

Arcane runs on the existing `docker_apps` LXC but is deliberately separate
from `/opt/homelab-compose`, the project tree it manages. Ansible owns this
Compose project at `/opt/homelab-control/arcane`; Arcane must never Git-sync or
update itself.

- Private UI: `https://arcane.home.hchu.me`
- Loopback recovery endpoint: `http://127.0.0.1:3552`
- Persistent state: `/srv/homelab/docker-apps/arcane/data`
- Managed projects: `/opt/homelab-compose/{platform,media,code,openclaw}`

The sibling `/opt/homelab-compose/openclaw-setup` private repository is mounted
read-only inside Arcane and is never registered as an Arcane project.

The Docker socket is mounted only into the private socket-proxy container.
The proxy reduces the exposed API surface, but Arcane can still create
containers and is therefore a trusted, effectively host-administrative control
plane.

Image updates, pruning, auto-heal, and lifecycle hooks are disabled during
Ansible reconciliation. Renovate and Git remain authoritative. See
`docs/runbooks/arcane.md` for bootstrap, rollback, backup, and recovery.
