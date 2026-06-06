# Secrets

Store real service secrets in SOPS-encrypted files or GitHub Actions secrets.

Expected encrypted values:

- `cloudflare_caddy_token`
- `cloudflare_adguard_acme_token`
- `cloudflare_ddns_token`
- `proton_wireguard_private_key`
- `proton_wireguard_address`
- `proton_wireguard_public_key`
- `proton_wireguard_endpoint`
- `qbittorrent_webui_password`
- `copyparty_users`
- `adguard_admin_password_hash`

Do not commit decrypted secret files.
