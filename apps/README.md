# Applications

Most application services run through Docker Compose on the `docker_apps` LXC.
OpenClaw uses its own immutable Compose runtime in the dedicated unprivileged
`openclaw` LXC; it is not part of the application-host stack.

- `compose/homelab`: the sole active application project: Traefik, AdGuard
  Home, Cloudflare DDNS, one direct qBittorrent instance, Copyparty, MeTube,
  and T3 Code.
- `images/t3code`: CI-only T3 Code image input. Production archives exclude
  this directory and deploy an approved exact OCI digest.

Ansible only prepares root-owned direct-deployment inputs under
`/etc/homelab/runtime/homelab` and service-scoped Cloudflare credentials under
`/etc/homelab/secrets`. The locked direct deployer owns activation and
previous-homelab rollback. Legacy cutover assets exist only as already staged
host releases for an offline/manual recovery procedure; the normal source,
deployment, and validation paths are homelab-only.

The public homelab repository is the deployment source for OpenClaw. Mutable
OpenClaw state and credentials stay outside the repository. See
`docs/runbooks/openclaw.md`.
