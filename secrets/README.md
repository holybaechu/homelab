# Secrets

Store real service secrets in SOPS-encrypted files or GitHub Actions secrets.

Expected encrypted values:

- `cloudflare_traefik_token`
- `cloudflare_ddns_token`
- `tailscale_auth_key`
- `qbittorrent_webui_password`
- `copyparty_users`, as a list of account objects with `name` and `password`
- `adguard_admin_password`, as plaintext; the AdGuard role hashes it before writing the service config
- `arcane_encryption_key`, exactly 64 hexadecimal characters representing 32 bytes
- `arcane_jwt_secret`, at least 32 characters
- `openclaw_gateway_token`, exactly 64 hexadecimal characters representing 32 bytes

GitHub Actions supplies the Arcane values as `ARCANE_ENCRYPTION_KEY` and
`ARCANE_JWT_SECRET`. Ansible renders root-owned mode-`0640` runtime files readable
only by Arcane's runtime GID under `/opt/homelab-control/arcane/secrets`, and
Arcane mounts them read-only. Keep
the GitHub values stable and preserve them with backups of
`/srv/homelab/docker-apps/arcane/data`. Never restore an existing database with
a new encryption key: encrypted registry credentials and other stored values
require the original key. Rotating or losing the JWT secret invalidates active
sessions.

GitHub Actions supplies `openclaw_gateway_token` as
`OPENCLAW_GATEWAY_TOKEN`. Ansible writes it outside Git at
`/opt/homelab-control/openclaw/secrets/gateway_token` as UID/GID 1000 mode
`0600`, and the Gateway mounts it read-only. Keep this value stable; rotating
it invalidates existing Gateway clients. OpenClaw's file-secret provider
rejects a group-readable mode such as `0640`.

Non-secret deployment values:

- `adguard_admin_username`, optional; defaults to `admin`

The CD helper validates both Arcane secrets and the OpenClaw Gateway token
before writing Ansible extra vars.
It accepts only a 64-character hexadecimal encryption key and a JWT secret of
at least 32 characters.

The active topology has no Gluetun or Proton VPN service and requires no
Proton or WireGuard credential.

Do not commit decrypted secret files.

Generate the OpenClaw token with `openssl rand -hex 32` and store only the
result in the GitHub `prod` environment secret `OPENCLAW_GATEWAY_TOKEN`.

For GitHub Actions, store the Copyparty accounts as `COPYPARTY_USERS_JSON`, for example:

```json
[{"name":"example","password":"replace-me"}]
```
