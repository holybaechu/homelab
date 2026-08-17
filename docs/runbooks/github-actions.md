# GitHub Actions CI/CD

This repository has two workflows:

- `.github/workflows/ci.yml`: repository tests, Ansible syntax checks, and OpenTofu validation for pushes and pull requests.
- `.github/workflows/cd.yml`: production deployment through Tailscale, OpenTofu, and Ansible.

Create a GitHub environment named `prod` before enabling CD. Pushes to `main`
deploy automatically; add an environment approval rule only if you intentionally
want production deployment to pause for review.

## Dependency Updates

Renovate creates reviewed source-update pull requests for versions stored in
Git. Minor and patch updates automerge only when the current version is not
`0.x`; major updates and every `0.x` update require review. Arcane Manager and
its Docker socket proxy are explicit control-plane exceptions: their updates
always require review. OpenClaw image updates also always require review.

Built-in managers cover supported package manifests. Custom regex managers
also cover the shared `.opentofu-version`, the annotated Tailscale GitHub
Action version, the pinned VueTorrent LinuxServer mod, Debian 13 Proxmox LXC
template versions. The Proxmox template uses its configured custom datasource.

Packages installed from apt repositories are runtime dependencies, not
Renovate dependencies when their versions are not recorded in Git. During each
deployment, Ansible refreshes apt metadata and upgrades the managed Debian,
Docker Engine and Compose plugin, and Tailscale packages to the repository
version available from their configured apt repositories.

## `prod` Environment Variables

- `PVE_NODE_NAME`: `pve`
- `PVE_BRIDGE`: `vmbr0`
- `PVE_ROOT_DATASTORE_ID`: `local-lvm`
- `PVE_TAILSCALE_IP`: Tailscale IP or MagicDNS name for the Proxmox node
- `TOFU_STATE_BUCKET`: S3-compatible bucket for OpenTofu state
- `TOFU_STATE_KEY`: state object key, for example `prod/opentofu.tfstate`
- `TOFU_STATE_REGION`: use `auto` for Cloudflare R2, or the AWS region for AWS S3
- `TOFU_STATE_ENDPOINT`: S3-compatible endpoint, for example `https://<account-id>.r2.cloudflarestorage.com` for Cloudflare R2
- `ADGUARD_ADMIN_USERNAME`: optional AdGuard Home admin username; defaults to the inventory value
The native OpenClaw reservation is not duplicated as a GitHub variable. Its
exact production identity (`192.168.0.5`, `02:00:00:BA:EC:05`) is hardcoded in
the tracked OpenTofu topology and enforced by the LXC preflight. Keep the
router reservation aligned with that tracked contract.

## `prod` Environment Secrets

- `PROXMOX_ENDPOINT`
- `PROXMOX_API_TOKEN`
- `DEPLOY_SSH_PUBLIC_KEYS`
- `DEPLOY_SSH_PRIVATE_KEY`
- `DEPLOY_SSH_KNOWN_HOSTS`, pinned OpenSSH `known_hosts` lines for the Proxmox SSH host
- `TOFU_STATE_ACCESS_KEY_ID`
- `TOFU_STATE_SECRET_ACCESS_KEY`
- `TS_OAUTH_CLIENT_ID`
- `TS_AUDIENCE`
- `CLOUDFLARE_TRAEFIK_TOKEN`
- `CLOUDFLARE_DDNS_TOKEN`
- `ADGUARD_ADMIN_PASSWORD`, as plaintext; Ansible hashes it before rendering or updating AdGuard Home config
- `TAILSCALE_AUTH_KEY`
- `QBITTORRENT_WEBUI_PASSWORD`
- `COPYPARTY_USERS_JSON`, as a JSON list of objects with `name` and plaintext `password`
- `ARCANE_ENCRYPTION_KEY`, exactly 64 hexadecimal characters representing 32 bytes
- `ARCANE_JWT_SECRET`, at least 32 characters
- `OPENCLAW_GATEWAY_TOKEN`, exactly 64 hexadecimal characters representing 32 bytes;
  the bearer token for the one Gateway
- `OPENCLAW_DISCORD_BOT_TOKEN`, required; the one shared bot token loaded by
  the one Gateway for direct channel routing.
- `OPENCLAW_EXA_API_KEY`, required; the file-backed credential for the pinned
  Exa web-search provider plugin.

The active topology has one direct qBittorrent instance and no Gluetun or
Proton VPN service, so CD does not require a Proton or WireGuard secret.

The Arcane Ansible role renders these stable values as mode-`0600` files under
`/opt/homelab-control/arcane/secrets`. Keep the GitHub values stable and
recoverable: Arcane's persistent database must always be restored with the
matching encryption key. The runtime files are not the recovery source and may
be recreated with the same values during an LXC replacement.

Generate the two values independently with `openssl rand -hex 32`; do not reuse
one output for both secrets.

Generate `OPENCLAW_GATEWAY_TOKEN` with `openssl rand -hex 32`. Ansible writes
it outside Git on VMID 118 as a root-owned mode-`0600` file below
`/etc/openclaw/secrets/`; the one Gateway receives it as a systemd credential.
Keep the value stable and recoverable; rotating it invalidates Gateway clients.

The Gateway uses the owner's `openai:main` ChatGPT/Codex profile; no OpenAI API
key belongs in GitHub. Do not put an API key, ChatGPT session token, OAuth
refresh token, or desktop `~/.codex` copy in GitHub secrets or the private
configuration. The `ctf` agent shares this profile in the one Gateway. Delete
obsolete `OPENCLAW_CTF_GATEWAY_TOKEN` and `OPENCLAW_CTF_OPENAI_API_KEY`
secrets after migration.

Do not create per-agent Discord secrets. The one
`OPENCLAW_DISCORD_BOT_TOKEN` is supplied directly to
`openclaw-gateway.service`; numeric channel bindings in the private config
select agents.

Example `COPYPARTY_USERS_JSON`:

```json
[{"name":"holybaechu","password":"replace-me"}]
```

`PROXMOX_API_TOKEN` must use the bpg/proxmox provider format:

```text
<user>@<realm>!<token-id>=<token-secret>
```

Do not include the Proxmox HTTP authorization prefix.

`DEPLOY_SSH_KNOWN_HOSTS` is written directly to the GitHub runner's
`~/.ssh/known_hosts` before Ansible connects to Proxmox. It only needs the
Proxmox host SSH key. During the normal full bootstrap, LXC SSH host keys are
collected through the pinned Proxmox connection with `pct exec` before the
runner connects to the guests.

When the optional workload-identity proof is enabled, its read-only baseline runs before bootstrap. The workflow therefore reads the Docker LXC's current Ed25519 host key through the already pinned Proxmox connection and adds it only to the ephemeral runner's `known_hosts` before taking the baseline. It never uses `ssh-keyscan` or trusts a key obtained directly from the network.

Generate the value from a trusted Proxmox console or an already-trusted SSH session:

```sh
awk '{print "192.168.0.2,pve,pve.home.hchu.me " $0}' /etc/ssh/ssh_host_ed25519_key.pub
```

The secret value should look like this single line:

```text
192.168.0.2,pve,pve.home.hchu.me ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... root@pve
```

If the Proxmox SSH host key is regenerated, update this secret before the next CD run.

## Tailscale Setup

Create a Tailscale federated identity for GitHub Actions and allow it to create ephemeral nodes tagged `tag:ci`. The workflow uses `oauth-client-id` plus `audience`, so it requires the GitHub workflow permission `id-token: write`.

The `tag:ci` ACL should only reach:

- Proxmox SSH/API
- LXC SSH targets at `192.168.0.4:22` (tailnet), `192.168.0.3:22`
  (Docker apps), `192.168.0.5:22` (the dedicated OpenClaw LXC), and
  `192.168.0.6:22` (the isolated CTF executor)

Verify the ephemeral CI node can reach all four SSH targets. The normal full
workflow fails closed during bootstrap if the ACL does not permit the OpenClaw
or CTF executor address.

## OpenTofu State

Create an S3-compatible bucket for remote state and enable versioning if the provider supports it. The deployment script uses OpenTofu's S3 backend with native `use_lockfile` locking, so no DynamoDB table is required.

The CI workflow validates OpenTofu with `tofu init -backend=false`; only CD needs the real remote-state credentials.
Container topology is tracked in `infra/opentofu/envs/prod/containers.auto.tfvars`.
Private provider values stay in ignored local tfvars files or the generated CI `ci.auto.tfvars.json`.

### Confirmed stale lock recovery

Use force-unlock only after the owning CD job has completed and no other CD run
is active. Copy the complete lock UUID from the failed OpenTofu log, open the
`cd` workflow with `workflow_dispatch`, and supply it as
`tofu_force_unlock_id`. The workflow validates the UUID, initializes the exact
production backend, removes that confirmed stale lock, and then performs the
normal plan and deployment. Leave the input empty for every normal deployment.

## CD Scope and Parallelism

CD selects exactly one of four scopes: `none`, `openclaw`, `arcane`, or `full`.
Documentation/test-only pushes use `none`; isolated native OpenClaw, CTF
executor, and CTF transport changes use `openclaw`; changes only under the
known safe `platform`, `media`, and `code` Compose trees use `arcane`. Static
`platform/traefik.yml` changes use the full path for forced recreation. The
exact `platform/dynamic/routes.yml` ownership tuple also uses the full path so
the pre-site OpenClaw source-hold, exclusive-owner, and route checks run before
any service mutation. After Tailscale connects, the runner pins
`arcane.home.hchu.me` to `192.168.0.3` in its ephemeral hosts file because the
Tailscale action uses `--accept-dns=false`.
The Arcane request still uses the HTTPS hostname for correct SNI and certificate
validation.

The runner deploys only affected projects through Arcane and requires the
expected commit before the normal Docker-host validation runs. Arcane
control-plane files, mixed pushes, ambiguous paths, and unknown infrastructure
changes use the safe `full` fallback.

Before the Arcane path, the serialized workflow force-updates the dedicated
`arcane-deploy` branch to the current `GITHUB_SHA` and verifies the remote ref.
Arcane syncs this CI-owned branch rather than mutable `main`, so a newer queued
push cannot change an older job's deployment source. The workflow has
`contents: write` only for this branch update; do not update `arcane-deploy`
outside this serialized workflow.

Arcane authentication uses GitHub OIDC, not a stored Arcane API key. Trust only
subject `repo:holybaechu/homelab:environment:prod` with audience
`https://arcane.home.hchu.me`, and map it to an environment-scoped deployment
role with only `gitops:list`, `gitops:read`, and `gitops:sync` permissions.

The `openclaw` path skips OpenTofu, global bootstrap, retained-Gateway fencing,
and unrelated application roles. It reconciles only the CTF executor, native
Gateway, credential-scoped transport, and their validations. Retained rollback
roles, Compose ownership, Traefik route changes, and LXC allocation changes map
to `full`, preserving the rollback boundary.

The full path keeps allocation preflights, OpenTofu, and bootstrap operations
serial. Service reconciliation is fail-fast in the order `tailnet`, native
`openclaw`, `docker_apps`, then Proxmox cleanup. This ensures tailnet recovery
precedes the new host and a future Traefik route cannot precede a ready native
Gateway. All applications within `docker_apps` remain ordered Compose projects;
Arcane is a separate Ansible-owned control project.

The native OpenClaw role reconciles VMID 118 with its integrity-pinned runtime,
nftables policy, and active system service. The tracked Traefik route points to
that LXC. The former Docker Gateway stays stopped as exact rollback material,
and its Arcane sync remains retired behind the permanent source hold.

The full path remains the bootstrap and break-glass recovery route for every
workload even though app-only pushes use Arcane.

Each service run uses `ansible-playbook --limit <service>` through `scripts/ci/run-ansible-parallel.sh`. GitHub logs are grouped per service, and the step fails if any service deploy or validation process fails.

## Arcane Bootstrap

The first Arcane deployment must use the normal full path so OpenTofu, the
shared data mount, Docker Engine, workload projects, and the Arcane control
project are reconciled in order. After deployment, complete the private
first-login flow and run the live validation gates in
`docs/runbooks/arcane.md` before using Arcane for workload operations.

## First Deployment

1. Push these workflow changes and confirm `ci` passes.
2. Set the one-time low-ID confirmation variable to `true`; the preflight
   hostname-verifies the affected containers and creates local `vzdump` archives
   before replacement.
3. Open the `cd` workflow and run it with `workflow_dispatch`.
4. Approve the `prod` environment deployment if protection rules are enabled.
5. Confirm the workflow completes `Prepare one-time lowest-ID cutover`,
   `OpenTofu apply`, `Bootstrap Proxmox and LXC access`, `Deploy services`,
   and `Validate services`.

The AdGuard role only writes the baseline `AdGuardHome.yaml` when no migrated
config exists. AdGuard owns and rewrites the existing runtime file, so changing
the administrator username, password, or template for an existing instance
requires an explicit reviewed migration; routine deploys preserve runtime state.

The qBittorrent role only bootstraps a missing configuration file. Existing
application-owned preferences, credentials, and runtime fields are preserved;
after startup, Ansible conditionally updates only VueTorrent's enabled state
and `/vuetorrent` root through qBittorrent's loopback Web API. The mod's entry
asset remains `/vuetorrent/public/index.html`.
