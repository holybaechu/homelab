# GitHub Actions CI/CD

This repository has two workflows:

- `.github/workflows/ci.yml`: repository tests, Ansible syntax checks, and OpenTofu validation for pushes and pull requests.
- `.github/workflows/cd.yml`: production deployment through Tailscale, OpenTofu, and Ansible.

Create a GitHub environment named `prod` before enabling CD. Use environment protection rules so a push to `main` cannot deploy without approval.

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

## `prod` Environment Secrets

- `PROXMOX_ENDPOINT`
- `PROXMOX_API_TOKEN`
- `DEPLOY_SSH_PUBLIC_KEYS`
- `DEPLOY_SSH_PRIVATE_KEY`
- `TOFU_STATE_ACCESS_KEY_ID`
- `TOFU_STATE_SECRET_ACCESS_KEY`
- `TS_OAUTH_CLIENT_ID`
- `TS_AUDIENCE`
- `CLOUDFLARE_CADDY_TOKEN`
- `CLOUDFLARE_ZONE_ID`
- `CLOUDFLARE_DDNS_TOKEN`
- `CLOUDFLARE_ADGUARD_ACME_TOKEN`
- `ADGUARD_ADMIN_PASSWORD`, as plaintext; Ansible hashes it before rendering or updating AdGuard Home config
- `TAILSCALE_AUTH_KEY`
- `PROTON_WIREGUARD_PRIVATE_KEY`
- `QBITTORRENT_WEBUI_PASSWORD`
- `HERMES_DISCORD_BOT_TOKEN`
- `HERMES_DISCORD_ALLOWED_USERS`
- `COPYPARTY_USERS_JSON`, as a JSON list of objects with `name` and `password`

Example `COPYPARTY_USERS_JSON`:

```json
[{"name":"holybaechu","password":"replace-me"}]
```

`PROXMOX_API_TOKEN` must use the bpg/proxmox provider format:

```text
<user>@<realm>!<token-id>=<token-secret>
```

Do not include the Proxmox HTTP authorization prefix.

## Tailscale Setup

Create a Tailscale federated identity for GitHub Actions and allow it to create ephemeral nodes tagged `tag:ci`. The workflow uses `oauth-client-id` plus `audience`, so it requires the GitHub workflow permission `id-token: write`.

The `tag:ci` ACL should only reach:

- Proxmox SSH/API
- LXC SSH targets on `192.168.0.3` through `192.168.0.9`

## OpenTofu State

Create an S3-compatible bucket for remote state and enable versioning if the provider supports it. The deployment script uses OpenTofu's S3 backend with native `use_lockfile` locking, so no DynamoDB table is required.

The CI workflow validates OpenTofu with `tofu init -backend=false`; only CD needs the real remote-state credentials.
Container topology is tracked in `infra/opentofu/envs/prod/containers.auto.tfvars`.
Private provider values stay in ignored local tfvars files or the generated CI `ci.auto.tfvars.json`.

## CD Parallelism

The CD workflow keeps OpenTofu and bootstrap operations serial, then runs Ansible service deploy and validation in parallel across `edge`, `dns`, `tailnet`, `downloads`, `files`, `minecraft`, and `hermes`.

Each service run uses `ansible-playbook --limit <service>` through `scripts/ci/run-ansible-parallel.sh`. GitHub logs are grouped per service, and the step fails if any service deploy or validation process fails.

## First Deployment

1. Push these workflow changes and confirm `ci` passes.
2. Open the `cd` workflow and run it with `workflow_dispatch`.
3. Approve the `prod` environment deployment if protection rules are enabled.
4. Confirm the workflow completes `OpenTofu apply`, `Bootstrap Proxmox and LXC access`, `Deploy services`, and `Validate services`.

The AdGuard role only writes the baseline `AdGuardHome.yaml` when no migrated config exists, but it updates the existing `users:` block from `ADGUARD_ADMIN_USERNAME` and `ADGUARD_ADMIN_PASSWORD` on each deploy.
