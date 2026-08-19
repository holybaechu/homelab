# Deployment Secrets

`infra/deployment/secrets.json` is the machine-readable contract for service
values passed from the GitHub `prod` environment to Ansible. Each entry names
its GitHub environment key, Ansible variable, owning component, required or
optional kind, and validation type. It is the only GitHub-to-Ansible mapping;
the workflow only exposes selected environment keys to the helper.

## Component contract

### `apps`

| GitHub environment name | Ansible variable | Kind | Validation |
| --- | --- | --- | --- |
| `CLOUDFLARE_TRAEFIK_TOKEN` | `cloudflare_traefik_token` | required | non-empty string |
| `CLOUDFLARE_DDNS_TOKEN` | `cloudflare_ddns_token` | required | non-empty string |
| `ADGUARD_ADMIN_PASSWORD` | `adguard_admin_password` | required | non-empty string |
| `QBITTORRENT_WEBUI_PASSWORD` | `qbittorrent_webui_password` | required | non-empty string |
| `COPYPARTY_USERS_JSON` | `copyparty_users` | required | non-empty JSON user list |
| `ADGUARD_ADMIN_USERNAME` | `adguard_admin_username` | optional | non-empty when supplied |

AdGuard hashes its plaintext admin password while rendering its service
configuration. `COPYPARTY_USERS_JSON` must be a JSON list of objects containing
non-empty `name` and plaintext `password` fields, for example:

```json
[{"name":"example","password":"replace-me"}]
```

### `tailnet`

| GitHub environment name | Ansible variable | Kind | Validation |
| --- | --- | --- | --- |
| `TAILSCALE_AUTH_KEY` | `tailscale_auth_key` | required | non-empty string |

### `openclaw`

| GitHub environment name | Ansible variable | Kind | Validation |
| --- | --- | --- | --- |
| `OPENCLAW_GATEWAY_TOKEN` | `openclaw_gateway_token` | required | exactly 64 hexadecimal characters |
| `OPENCLAW_DISCORD_BOT_TOKEN` | `openclaw_discord_bot_token` | required | non-empty string |
| `OPENCLAW_EXA_API_KEY` | `openclaw_exa_api_key` | required | 1–4096 non-whitespace characters |
| `OPENCLAW_SKILL_SYNC_GITHUB_TOKEN` | `openclaw_skill_sync_github_token` | required | 20–4096 non-whitespace characters |

Generate `OPENCLAW_GATEWAY_TOKEN` with `openssl rand -hex 32`. Keep every real
value only in the GitHub `prod` environment or its intended runtime secret
store; do not commit generated extra-vars files.

## Scoped writer

The writer requires an explicit comma-separated component set:

```sh
python3 scripts/ci/write_ansible_extra_vars.py \
  /path/to/ansible-extra-vars.json \
  apps,tailnet,openclaw
```

It reads only entries owned by the selected components, unions mixed component
sets, omits unset optional values, rejects empty or unknown component names,
and validates values before publishing the JSON by same-directory atomic
replacement. The resulting file is mode `0600`. There is no aggregate legacy
scope alias.
