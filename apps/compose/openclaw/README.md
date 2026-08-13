# Retained Docker OpenClaw Gateway

This is the current Docker deployment interface during the native-LXC
transition. After the audited cutover it is retained, stopped, as an exact
break-glass rollback asset. The homelab repository owns the image pin,
container hardening, mounts, port, and lifecycle. It does not contain the
OpenClaw configuration or credentials.

- Private configuration repo: `/opt/homelab-compose/openclaw-setup`
- Active host config: `openclaw-setup/config/openclaw.json`
- Active container config: `/etc/openclaw/openclaw.json`
- Runtime state: `/srv/homelab/docker-apps/openclaw`
- Gateway token: `/opt/homelab-control/openclaw/secrets/gateway_token`
- Host endpoint: `http://127.0.0.1:18789`

The Gateway binds `lan` inside its Docker network because Docker cannot forward
a published port to an in-container loopback listener. Docker publishes that
port only on host loopback. During native staging, Traefik points at the
unavailable native LXC while this source Gateway remains otherwise isolated.
A tracked rollback attaches only the retained container to `homelab_proxy` as
`openclaw-rollback` and routes Traefik to that alias; authentication remains
mandatory through a file-backed OpenClaw SecretRef.

The upstream image already uses `tini` as its entrypoint, so Compose does not
add a second init process. The container runs as UID/GID 1000 with a read-only
root filesystem, all capabilities dropped, and only state, auth-profile state,
and `/tmp` writable.

Arcane manages this project from the public repository only until the cutover
finalizer retires its OpenClaw sync. It must never Git-sync or edit the sibling
private `openclaw-setup` repository. After phase 2, rollback changes use the
full Ansible path; neither rollback nor restoration re-registers the project
with Arcane. See `docs/runbooks/openclaw-native-migration.md` for the tracked
three-file rollback transaction.
