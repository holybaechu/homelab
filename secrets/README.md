# Secrets

Store real service secrets in SOPS-encrypted files or GitHub Actions secrets.

Expected encrypted values:

- `cloudflare_traefik_token`
- `cloudflare_ddns_token`
- `proton_wireguard_private_key`
- `tailscale_auth_key`
- `qbittorrent_webui_password`
- `copyparty_users`, as a list of account objects with `name` and `password`
- `adguard_admin_password`, as plaintext; the AdGuard role hashes it before writing the service config
- `arcane_encryption_key`, exactly 64 hexadecimal characters representing 32 bytes
- `arcane_jwt_secret`, at least 32 characters

GitHub Actions supplies the Arcane values as `ARCANE_ENCRYPTION_KEY` and
`ARCANE_JWT_SECRET`. Ansible renders root-owned mode-`0640` runtime files readable
only by Arcane's runtime GID under `/opt/homelab-control/arcane/secrets`, and
Arcane mounts them read-only. Keep
the GitHub values stable and preserve them with backups of
`/srv/homelab/docker-apps/arcane/data`. Never restore an existing database with
a new encryption key: encrypted registry credentials and other stored values
require the original key. Rotating or losing the JWT secret invalidates active
sessions.

Non-secret deployment values:

- `adguard_admin_username`, optional; defaults to `admin`

The CD helper validates both Arcane secrets before writing Ansible extra vars.
It accepts only a 64-character hexadecimal encryption key and a JWT secret of
at least 32 characters.

Do not commit decrypted secret files.

For GitHub Actions, store the Copyparty accounts as `COPYPARTY_USERS_JSON`, for example:

```json
[{"name":"example","password":"replace-me"}]
```
