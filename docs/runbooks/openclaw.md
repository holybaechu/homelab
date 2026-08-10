# OpenClaw Foundation

## Ownership and paths

OpenClaw is intentionally split across one public deployment repository, one
private configuration repository, and non-Git runtime storage:

| Concern | Location |
| --- | --- |
| Public Compose and Ansible deployment | `apps/compose/openclaw` in `homelab` |
| Deployed Compose project | `/opt/homelab-compose/openclaw` |
| Private OpenClaw repository | `/opt/homelab-compose/openclaw-setup` |
| Active host config | `/opt/homelab-compose/openclaw-setup/config/openclaw.json` |
| Active container config | `/etc/openclaw/openclaw.json` |
| Persistent runtime state | `/srv/homelab/docker-apps/openclaw/state` |
| Future auth-profile state | `/srv/homelab/docker-apps/openclaw/auth-profile-secrets` |
| Gateway token | `/opt/homelab-control/openclaw/secrets/gateway_token` |

The private repo contains config plus inert scaffolding for future agents,
workspaces, and shared skills. It contains no deployment manifest. Runtime
state, credentials, sessions, databases, logs, and caches belong in neither
Git repository.

## Deployment contract

The Gateway uses the immutable official
`ghcr.io/openclaw/openclaw:2026.7.1-2` image index digest recorded in Compose.
It runs as UID/GID 1000 with a read-only root filesystem, all Linux
capabilities dropped, and `no-new-privileges`. The image already enters through
`tini`, so Compose does not add a second init process.

Docker publishes only `127.0.0.1:18789:18789`. OpenClaw binds `lan` inside the
container because Docker bridge forwarding cannot reach an in-container
loopback listener. The host remains loopback-only. There is no Traefik route,
firewall change, or Docker socket mount.

Gateway authentication is mandatory. The tracked config holds only a file
SecretRef; Ansible installs the 64-hex token outside Git as UID/GID 1000 mode
`0600`, and Docker mounts it read-only. OpenClaw rejects group-readable secret
files, so mode `0640` is not sufficient for this provider.

The Docker systemd service supplies reboot/logout persistence, and the
Gateway container uses `restart: unless-stopped`. There is no host-native
OpenClaw systemd unit because Docker/Arcane was the selected deployment mode.

## Bootstrap and updates

The first deployment must use the full homelab CD path. Its order is:

1. Reconcile the existing Compose workloads.
2. Run the isolated `openclaw_foundation` role.
3. Register/adopt the public `openclaw` project in Arcane.
4. Run the live validation playbook.

The OpenClaw role never loops over or force-recreates platform, media, or code.
After bootstrap, changes solely below `apps/compose/openclaw` use the Arcane
fast path. Updates to the private repo are deliberately manual: commit the
config, validate it, and restart only `openclaw-gateway`. Do not configure
automatic Git pull or push.

Before committing a private config change:

```sh
cd /opt/homelab-compose/openclaw
docker compose run -T --rm --no-deps --entrypoint node \
  openclaw-gateway dist/index.js config file
docker compose run -T --rm --no-deps --entrypoint node \
  openclaw-gateway dist/index.js config validate --json
docker compose run -T --rm --no-deps --entrypoint node \
  openclaw-gateway dist/index.js secrets audit --check --json
```

The `config file` command intentionally has no `--json` flag in this release.
After the private commit, restart only the Gateway:

```sh
cd /opt/homelab-compose/openclaw
docker compose restart openclaw-gateway
```

## Verification

Run the production validation playbook, or verify directly:

```sh
cd /opt/homelab-compose/openclaw
docker compose ps
docker compose exec -T openclaw-gateway node dist/index.js --version
docker compose exec -T openclaw-gateway node dist/index.js config file
docker compose exec -T openclaw-gateway node dist/index.js config validate --json
docker compose exec -T openclaw-gateway node dist/index.js secrets audit --check --json
docker compose exec -T openclaw-gateway node dist/index.js gateway probe --json
curl -fsS http://127.0.0.1:18789/healthz
curl -fsS http://127.0.0.1:18789/readyz
ss -H -ltn 'sport = :18789'
```

Expected results include a healthy container, CLI `OpenClaw 2026.7.1`, active
config `/etc/openclaw/openclaw.json`, a clean secrets audit, and exactly one
host listener on `127.0.0.1:18789`.

## Arcane boundary

Arcane manages only the public `openclaw` Compose project. Its broad projects
mount remains read-write, but a nested bind remounts
`/opt/homelab-compose/openclaw-setup` read-only inside Arcane. This prevents
ordinary Arcane editor/sync operations from modifying the private checkout.
Arcane remains effectively host-administrative through the permitted Docker
API, so the read-only overlay is an accidental-change guard, not a security
boundary against a compromised administrator.

Use Arcane for OpenClaw Up, Down, Restart, and Redeploy. Do not Rename or
Destroy the private sibling checkout, and never register `openclaw-setup` as a
GitOps project.

## Backup and recovery

Back up these as separate recovery units:

- the private `openclaw-setup` Git remote once configured;
- `/srv/homelab/docker-apps/openclaw` for runtime state;
- the stable `OPENCLAW_GATEWAY_TOKEN` in the GitHub `prod` environment.

The rendered token file is not its authoritative backup. Rotating the GitHub
secret and running a full deployment recreates only OpenClaw, but invalidates
clients using the old Gateway token.

No model, Codex/OpenAI authentication, custom agent, channel, subagent, skill,
or self-learning behavior belongs in this foundation deployment.
