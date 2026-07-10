# Hermes Agent Docker Runbook

Hermes runs in the `hermes` Compose project on `docker_apps`. The image is
based on the official `nousresearch/hermes-agent` release and adds only the
1Password CLI used by the existing Newrrow skill.

Persistent paths:

- `/srv/homelab/hermes/home` -> `/opt/data`
- `/srv/homelab/hermes/workspace` -> `/workspace`

The Discord gateway is outbound-only. There is no public webhook or dashboard
route and the Docker socket is not mounted.

Inspect:

```bash
ssh root@192.168.0.3 \
  'cd /opt/homelab-compose/hermes && docker compose ps && docker compose exec hermes hermes status'
```

Browser automation receives a 1 GB shared-memory allocation. All Discord,
search, browser, Honcho, and 1Password credentials are rendered into the
root-only Compose `.env` by Ansible during CD.

To upgrade, update the pinned official image tag in both the Dockerfile and
local image name, then merge through CI/CD. The bind-mounted state survives
image rebuilds.
