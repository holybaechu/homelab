# OpenClaw Gateway

This is the public deployment interface for the private OpenClaw foundation.
The homelab repository owns the image pin, container hardening, mounts, port,
and lifecycle. It does not contain the OpenClaw configuration or credentials.

- Private configuration repo: `/opt/homelab-compose/openclaw-setup`
- Active host config: `openclaw-setup/config/openclaw.json`
- Active container config: `/etc/openclaw/openclaw.json`
- Runtime state: `/srv/homelab/docker-apps/openclaw`
- Gateway token: `/opt/homelab-control/openclaw/secrets/gateway_token`
- Host endpoint: `http://127.0.0.1:18789`

The Gateway binds `lan` inside its Docker network because Docker cannot
forward a published port to an in-container loopback listener. Docker publishes
that port only on host loopback, and no Traefik route or firewall rule exposes
it. Authentication remains mandatory through a file-backed OpenClaw SecretRef.

The upstream image already uses `tini` as its entrypoint, so Compose does not
add a second init process. The container runs as UID/GID 1000 with a read-only
root filesystem, all capabilities dropped, and only state, auth-profile state,
and `/tmp` writable.

Arcane manages this project from the public homelab Git repository. It must
not Git-sync or edit the sibling private `openclaw-setup` repository. See
`docs/runbooks/openclaw.md` for deployment and verification.
