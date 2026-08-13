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
| Future auth-profile state | `/home/openclaw/.config/openclaw` |
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

No model, Codex/OpenAI authentication, custom agent, channel, subagent, skill,
or self-learning behavior belongs in this foundation deployment.
