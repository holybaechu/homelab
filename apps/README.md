# Applications

Most application services run through Docker Compose on the `docker_apps` LXC.
OpenClaw is in a staged migration to a native service in its dedicated
unprivileged LXC.

- `compose/platform`: Traefik, AdGuard Home, Cloudflare DDNS.
- `compose/media`: one direct qBittorrent instance at
  `https://qbt.home.hchu.me`, Copyparty, and MeTube.
- `compose/code`: a Kali-based T3 Code environment at
  `https://code.home.hchu.me`.
- `compose/openclaw`: the retained loopback-only Docker OpenClaw Gateway. It
  remains the active source during the transition, then becomes an inactive,
  exact-identity rollback asset after native cutover. Its private
  configuration lives in the separate `openclaw-setup` repository.
- `compose/arcane`: the Ansible-owned Arcane Docker management control plane.

Ansible bootstraps the workload projects at `/opt/homelab-compose` and renders
their private `.env` and configuration files. During migration, safe app-only
pushes still deploy registered projects through Arcane. The cutover finalizer
retires only Arcane's OpenClaw sync. The phase-2 commit then removes the
OpenClaw fast-path selector so later rollback-manifest changes take the full,
marker-aware Ansible path. Mixed, control-plane, and infrastructure changes
always use the full path.

The public homelab repository is the only deployment source for OpenClaw. The
private `/opt/homelab-compose/openclaw-setup` Git repository owns only active
config and future agent/workspace/skill definitions. Mutable OpenClaw state and
credentials are outside both repositories. See `docs/runbooks/openclaw.md`.

Arcane is deployed separately at `/opt/homelab-control/arcane`, with persistent
state under `/srv/homelab/docker-apps/arcane`. It is never one of its own
managed projects; Ansible remains its deployment and break-glass recovery path.
See `docs/runbooks/arcane.md` for the ownership, backup, and update policy.
