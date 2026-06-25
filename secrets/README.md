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
- `hermes_discord_bot_token`
- `hermes_discord_allowed_users`
- `hermes_parallel_api_key`
- `hermes_firecrawl_api_key`
- `hermes_browserbase_api_key`
- `hermes_browserbase_project_id`
- `hermes_1password_service_account_token`
- `copyparty_users`, as a list of account objects with `name` and `password`
- `adguard_admin_password`, as plaintext; the AdGuard role hashes it before writing the service config

Non-secret deployment values:

- `adguard_admin_username`, optional; defaults to `admin`

GitHub Actions secret names for the Hermes web, browser, and 1Password backends are `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `BROWSERBASE_API_KEY`, `BROWSERBASE_PROJECT_ID`, and `OP_SERVICE_ACCOUNT_TOKEN`; the CD helper maps them to `hermes_parallel_api_key`, `hermes_firecrawl_api_key`, `hermes_browserbase_api_key`, `hermes_browserbase_project_id`, and `hermes_1password_service_account_token` for Ansible.

Do not commit decrypted secret files.

For GitHub Actions, store the Copyparty accounts as `COPYPARTY_USERS_JSON`, for example:

```json
[{"name":"example","password":"replace-me"}]
```
