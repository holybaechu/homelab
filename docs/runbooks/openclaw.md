# OpenClaw Foundation

OpenClaw runs as a native system Gateway in dedicated unprivileged LXC VMID
118. The audited migration from the isolated Docker foundation is complete;
see `openclaw-native-migration.md` for the completed cutover and rollback
contract. Native is primary and the exact Docker container and assets remain
stopped under a permanent source hold. A tracked rollback never removes that
hold or restores Arcane ownership.

## Ownership and paths

The steady-state foundation separates public deployment code, one private
content repository, and mutable runtime material:

| Concern | Location |
| --- | --- |
| Public IaC, systemd, firewall, and Traefik deployment | `homelab` repository |
| Private OpenClaw repository | `/home/openclaw/openclaw-setup` on VMID 118 |
| Active native config | `/home/openclaw/openclaw-setup/config/openclaw.json` |
| Runtime state | `/var/lib/openclaw` |
| Core Codex OAuth-profile state | `/home/openclaw/.config/openclaw` |
| Gateway token | `/etc/openclaw/secrets/gateway_token` |

The private repository contains config plus inert scaffolding for future
agents, workspaces, and shared skills. Runtime credentials, sessions,
databases, logs, caches, and model authentication belong in neither Git
repository. The config is a regular root-owned file, not a symlink.

## Native service contract

Ansible installs integrity-pinned Node.js and `openclaw` npm releases under
versioned root-owned prefixes and runs the Gateway as the nologin `openclaw`
UID/GID 1000 account. A system-level `openclaw-gateway.service` supplies
reboot/logout persistence; OpenClaw's service repair and automatic update paths
are disabled because IaC is the external supervisor.

The service depends on nftables and cannot start without the firewall. The
Gateway binds `192.168.0.5:18789`; guest nftables accepts that port only from
Traefik at `192.168.0.3`. Traefik terminates HTTPS for
`openclaw.home.hchu.me`. Token authentication remains mandatory, the exact
HTTPS origin is allowlisted, proxy trust is limited to `192.168.0.3`, real-IP
fallback and Tailscale authentication are disabled, and the web terminal is
disabled.

Control UI access uses HTTPS. A new browser must receive the Gateway token out
of band and may require explicit device approval. Do not enable insecure auth,
device-auth bypasses, wildcard origins, or trusted-proxy authentication.

All agents, including `ctf`, inherit `openai/gpt-5.6-terra` with xhigh thinking
through `agents.defaults` and run through the native Codex runtime. They share
the one `openai:main` profile inside the one Gateway service account. No OAuth
file, desktop `~/.codex` directory, or OpenAI API key is stored in either Git
repository.

## Unified workspaces and autonomous skill promotion

OpenClaw keeps mutable state under `/var/lib/openclaw` and uses one explicit
workspace subtree per agent:

- `main`: `/var/lib/openclaw/workspaces/main`
- `ctf`: `/var/lib/openclaw/workspaces/ctf`

Only the CTF workspace and
`/var/lib/openclaw/sandbox/skills-workspaces` are bind-mounted at the same paths
on the remote executor. The rest of `/var/lib/openclaw` remains local to the
Gateway, so sessions, auth state, registries, and unrelated agent data are not
mirrored.

Skill Workshop runs with autonomous mode and automatic approval. Runtime skill
changes stay in the live `skills/` directories. Every five minutes,
`openclaw-skill-sync.timer` starts a separate `openclaw-skill-sync` identity
that can read those two skill roots but cannot write them or read Gateway auth
state. It validates the files, clones `holybaechu/openclaw-setup`, opens a bot
branch and pull request, waits for the private repository checks, and squash
merges the PR automatically. No manual PR review is part of the steady-state
flow.

Before deployment, create a fine-grained GitHub token restricted to the private
`holybaechu/openclaw-setup` repository with **Contents: read/write**, **Pull
requests: read/write**, and **Actions: read**, then store it in the homelab
production environment as `OPENCLAW_SKILL_SYNC_GITHUB_TOKEN`. The token is
loaded only by the promotion oneshot service through a systemd credential; it
is not exposed to the Gateway process.

The same credential lets the Ansible role fetch canonical `main` into the
protected, remote-free production checkout at the start of each homelab
deployment. The role refuses a dirty checkout, fetches by URL without adding a
persistent Git remote, stops the Gateway only when the commit changes, resets
to the fetched commit, and reapplies the protected ownership and modes. Skill
promotion itself updates GitHub immediately; the already-live skill stays in
the workspace, while the inert production checkout receives that promoted
commit on the next homelab deployment.

The first deployment migrates the legacy main workspace from
`/var/lib/openclaw/workspace` to `/var/lib/openclaw/workspaces/main`. It fails
closed if both locations contain data. The old `/srv/openclaw-ctf` mount is
accepted only during the preflight transition and is reconciled to
`/var/lib/openclaw/workspaces/ctf` by the LXC configuration stage.

## Core Codex subscription activation

After a deployment has installed the pinned `@openclaw/codex` harness, create
the shared profile from a trusted shell on VMID 118. Stop the one Gateway
service first.

```sh
sudo systemctl stop openclaw-gateway.service

sudo -u openclaw env \
  HOME=/home/openclaw \
  OPENCLAW_HOME=/home/openclaw \
  OPENCLAW_STATE_DIR=/var/lib/openclaw \
  OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json \
  OPENCLAW_WORKSPACE_DIR=/var/lib/openclaw/workspaces/main \
  PATH=/opt/nodejs/current/bin:/opt/openclaw/current/bin:/usr/local/bin:/usr/bin:/bin \
  /opt/nodejs/current/bin/node \
  /opt/openclaw/current/lib/node_modules/openclaw/openclaw.mjs \
  models auth login --provider openai --profile-id openai:main --device-code

sudo -u openclaw env \
  HOME=/home/openclaw \
  OPENCLAW_HOME=/home/openclaw \
  OPENCLAW_STATE_DIR=/var/lib/openclaw \
  OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json \
  OPENCLAW_WORKSPACE_DIR=/var/lib/openclaw/workspaces/main \
  PATH=/opt/nodejs/current/bin:/opt/openclaw/current/bin:/usr/local/bin:/usr/bin:/bin \
   /opt/nodejs/current/bin/node \
   /opt/openclaw/current/lib/node_modules/openclaw/openclaw.mjs \
   models auth list --agent main --provider openai

sudo -u openclaw env \
  HOME=/home/openclaw \
  OPENCLAW_HOME=/home/openclaw \
  OPENCLAW_STATE_DIR=/var/lib/openclaw \
  OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json \
  OPENCLAW_WORKSPACE_DIR=/var/lib/openclaw/workspaces/main \
  PATH=/opt/nodejs/current/bin:/opt/openclaw/current/bin:/usr/local/bin:/usr/bin:/bin \
  /opt/nodejs/current/bin/node \
  /opt/openclaw/current/lib/node_modules/openclaw/openclaw.mjs \
  models list --provider openai

sudo systemctl start openclaw-gateway.service
sudo systemctl is-active --quiet openclaw-gateway.service
```

Complete the displayed device-code flow in a browser using the owner's
ChatGPT/Codex account. The profile lives only in the core service account's
auth/state storage and refreshes there. Never put its device code, access or
refresh token, a ChatGPT session token, or an OpenAI API key in Git, GitHub
secrets, chat, or the private configuration. Before restarting the service,
confirm the model list includes `openai/gpt-5.6-terra`; do not substitute a
different model silently if the subscription does not expose Terra.

## Configuration updates

Edit only the private checkout and inspect the staged files as root. Then make
a temporary service-user credential copy outside Git and run the published,
pinned CLI as the service account before committing:

```sh
set -eu
credential_dir="$(mktemp -d /run/openclaw-config-audit.XXXXXX)"
trap 'rm -rf -- "$credential_dir"' EXIT HUP INT TERM
chown root:openclaw "$credential_dir"
chmod 0750 "$credential_dir"
install -o openclaw -g openclaw -m 0400 \
  /etc/openclaw/secrets/gateway_token \
  "$credential_dir/openclaw_gateway_token"
sudo -u openclaw -H env \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json \
  OPENCLAW_STATE_DIR=/var/lib/openclaw \
  OPENCLAW_GATEWAY_TOKEN_FILE="$credential_dir/openclaw_gateway_token" \
  /usr/local/bin/openclaw config validate --json
sudo -u openclaw -H env \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json \
  OPENCLAW_STATE_DIR=/var/lib/openclaw \
  OPENCLAW_GATEWAY_TOKEN_FILE="$credential_dir/openclaw_gateway_token" \
  /usr/local/bin/openclaw secrets audit --check --json
```

Commit through the private repo, then reconcile the public Ansible role. Do not
configure automatic Git pull or push, and do not use Control UI/Doctor config
writers against the protected file.

## Verification

The production validation must prove:

- `openclaw-gateway.service` is enabled, active, and bound only as intended;
- nftables permits port 18789 only from Traefik;
- the active config path is the private regular file and schema validation has
  no warnings;
- `secrets audit --check` is clean and the token is absent from Git;
- runtime/auth state is outside the private checkout;
- `https://openclaw.home.hchu.me` serves the Control UI through Traefik; and
- the retained Docker Gateway is stopped while its rollback assets and
  permanent source hold remain, or, only in tracked rollback state, that the
  native service is disabled with no listener and the exact retained container
  is the sole healthy Gateway.

The pinned core Codex harness and its `openai:main` profile are the only model
authentication contract in this foundation deployment. Custom agent routing,
channel policy, subagents, skills, and self-learning behavior remain outside
this foundation boundary.
