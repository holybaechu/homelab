# Applications

`compose/homelab` is the complete application release package for the
`docker_apps` LXC. It contains the Compose model, static configuration,
component-secret materializer, and semantic smoke test for Traefik, AdGuard
Home, Cloudflare DDNS, qBittorrent, Copyparty, and MeTube.

The package consumes one versioned `/etc/homelab/secrets/apps.json` bundle.
It does not depend on Ansible-rendered application files or a repository-level
environment overlay. Compose owns the shared `homelab_proxy` network, while
long-lived application data remains in explicitly named volumes and host
mounts.

OpenClaw uses a separate immutable Compose release in the dedicated
unprivileged `openclaw` LXC. Its mutable state and credentials stay outside
this public repository. See `docs/runbooks/openclaw.md`.
