# Secrets

Store real service secrets in SOPS-encrypted files or GitHub Actions secrets.

Expected encrypted values:

- `cloudflare_caddy_token`
- `cloudflare_zone_id`
- `cloudflare_adguard_acme_token`
- `cloudflare_ddns_token`
- `proton_wireguard_private_key`
- `tailscale_auth_key`
- `qbittorrent_webui_password`
- `copyparty_users`, as a list of account objects with `name` and `password`
- `adguard_admin_password_hash`

Do not commit decrypted secret files.
