# Arcane Docker Management

## Ownership contract

Arcane runs on the existing `docker_apps` LXC as a Docker management control
plane. It does not replace the infrastructure recovery path:

- OpenTofu owns the `docker_apps` LXC shape.
- Ansible owns Docker Engine, the Arcane control Compose project, Arcane's
  bootstrap secrets, and the rendered workload files.
- Arcane deploys and operates the `platform`, `media`, `code`, and `openclaw` Compose
  projects under `/opt/homelab-compose` for app-only pushes.
- Renovate and reviewed Git changes own container image versions. Arcane's
  image updater, automatic pruning, auto-heal, and lifecycle hooks remain
  disabled.

Arcane must never Git-sync, update, or destroy its own control project. Ansible
deploys that project separately at `/opt/homelab-control/arcane`.

## Endpoints and persistent paths

- Private UI: `https://arcane.home.hchu.me`
- Host-local recovery endpoint: `http://127.0.0.1:3552`
- Managed workload projects: `/opt/homelab-compose`
- Persistent Arcane state: `/srv/homelab/docker-apps/arcane/data`
- Runtime-group-only secrets: `/opt/homelab-control/arcane/secrets`
- Private OpenClaw setup checkout: `/opt/homelab-compose/openclaw-setup` (read-only in Arcane)

The UI is routed through Traefik's existing private-only middleware. Port 3552
is bound only to loopback for recovery from the Docker host, and the Docker
socket proxy is not published on the host.

## Bootstrap and first login

1. Add stable `ARCANE_ENCRYPTION_KEY` and `ARCANE_JWT_SECRET` values to the
   GitHub `prod` environment, then run the normal full Ansible deployment.
   Ansible creates the persistent state and runtime-group-only secret
   directories, renders those values as root-owned mode-`0640` files readable
   by Arcane's runtime GID, and starts the `arcane-control`
   Compose project.
2. Confirm both `arcane` and `docker-socket-proxy` are running and Arcane's
   container health check is healthy.
3. Open `https://arcane.home.hchu.me` from the LAN or tailnet and complete the
   first-login flow. Replace the initial administrator password immediately.
4. Confirm that Arcane discovers `platform`, `media`, `code`, and `openclaw` from
   `/opt/homelab-compose` and does not list `arcane-control` as a managed
   project.
5. Run the repository validation playbook before accepting Arcane as an
   operational deployment path.

Generate each secret independently and store the two outputs under their
matching GitHub environment secret names:

```sh
openssl rand -hex 32
openssl rand -hex 32
```

Each command produces 32 random bytes encoded as the required 64 hexadecimal
characters. Do not reuse one output for both settings.

If Traefik is unavailable, connect to the Docker host through the private SSH
path and use the loopback endpoint for diagnosis. Do not publish port 3552 on
the LAN or internet as a workaround.

## Operating workload projects

The project directory is mounted into Arcane at the identical absolute path so
Docker bind mounts and relative configuration files resolve consistently.
Ansible owns each deployment project's `.env` and generated files. OpenClaw's
active application config is the deliberate exception: it comes from the
separate private `openclaw-setup` Git repository, which Arcane sees through a
nested read-only mount. Do not edit either source in Arcane.

For a push containing only safe files under `apps/compose/platform`,
`apps/compose/media`, `apps/compose/code`, or `apps/compose/openclaw`, CD
selects the `arcane` scope.
`platform/traefik.yml` is a deliberate exception because it needs a forced
Traefik recreation, so it uses the full Ansible path.
After joining the tailnet, the runner pins `arcane.home.hchu.me` to
`192.168.0.3` in its ephemeral hosts file because Tailscale DNS acceptance is
disabled. HTTPS still uses the Arcane hostname, SNI, and certificate.

The deployment helper sends only the selected projects, requires the expected
Git commit, and waits for Arcane to finish before semantic validation runs.
Mixed changes, Arcane control-plane changes, Ansible templates, and
infrastructure changes use the full OpenTofu/Ansible path. Retain that full
path as break-glass recovery.

Arcane syncs the CI-owned `arcane-deploy` branch, not mutable `main`. At the
start of every serialized CD run, GitHub Actions force-updates only that branch
to `GITHUB_SHA` and verifies the remote ref before deployment. The workflow's
`contents: write` permission exists solely for this ref update. Do not update
`arcane-deploy` manually or configure another workflow to write it; the
serialized ref is what prevents a queued newer push from changing the commit
an older Arcane job deploys.

CI authenticates to Arcane with a short-lived GitHub OIDC token; no Arcane API
key is stored in GitHub. Configure Arcane to trust only:

- Issuer: `https://token.actions.githubusercontent.com`
- Subject: `repo:holybaechu/homelab:environment:prod`
- Audience: `https://arcane.home.hchu.me`
- Arcane URL: `https://arcane.home.hchu.me`

Map that identity to an environment-scoped, least-privilege deployment role
with only `gitops:list`, `gitops:read`, and `gitops:sync`. It must not manage
users, credentials, the Arcane control plane, projects directly, or unrelated
environments.

Do not enable repository-controlled lifecycle hooks without a separate review.
They execute code from the synchronized repository with access deliberately
granted to the hook runner.

## Update policy

Every managed service carries `com.getarcaneapp.arcane.updater=false`, so an
Arcane UI setting cannot silently bypass Renovate. Arcane Manager and the
Docker socket proxy are pinned in Compose and Renovate may open update pull
requests for them, but those two control-plane dependencies never automerge.

Review Arcane release and migration notes before accepting an update. Deploy
the update through Ansible, verify the control-plane health check and private
route, and then verify every workload project remains visible.

## Backup and recovery

Arcane's database and the stable secrets that encrypt it are a single recovery
unit. Back up `/srv/homelab/docker-apps/arcane/data` and preserve the matching
`ARCANE_ENCRYPTION_KEY` and `ARCANE_JWT_SECRET` values in the GitHub `prod`
environment or another authorized secret manager. The runtime files under
`/opt/homelab-control/arcane/secrets` are re-rendered copies, not the recovery
source.

The shared bind mount makes the database survive an ordinary container or LXC
root-filesystem replacement, but it is not an off-host backup. Use a
SQLite-consistent backup method or stop Arcane briefly before copying its state.

Never regenerate the encryption key while restoring an existing database.
Without the matching key, encrypted registry credentials and other stored
secrets may be unreadable. Losing the JWT secret invalidates active sessions.

For break-glass recovery:

1. Stop only the Arcane control project from
   `/opt/homelab-control/arcane`; do not stop workload projects.
2. Restore the state and make the original GitHub secret values available to
   Ansible.
3. Re-run the Arcane Ansible role or the full site playbook.
4. Verify container health, the private route, login, and workload discovery.

If Arcane itself is unusable, operate the workload Compose projects directly
from `/opt/homelab-compose` through the existing Ansible/SSH path.

For an ordinary bad workload release, revert the offending Git commit and push
the revert to `main`; the resulting app-only deployment must run through Arcane
and pass semantic validation. Use the full Ansible path only when Arcane or its
authentication/deployment path is unavailable.

## Security boundary

Only the socket-proxy container mounts `/var/run/docker.sock`, read-only. The
proxy disables Docker API groups Arcane does not need and is reachable only on
its internal Docker network. This reduces accidental API exposure but does not
make Arcane unprivileged: the allowed container, image, build, network, and
volume operations are still effectively host-administrative. Keep Arcane
private, use a strong administrator password, and grant access only to trusted
operators.
