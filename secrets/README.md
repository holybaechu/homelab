# Secrets

Store real service secrets in SOPS-encrypted files or GitHub Actions secrets.

Expected encrypted values:

- `cloudflare_traefik_token`
- `cloudflare_ddns_token`
- `proton_wireguard_private_key`
- `tailscale_auth_key`
- `qbittorrent_webui_password`
- `hermes_discord_bot_token`
- `hermes_discord_allowed_users`
- `hermes_discord_home_channel`, optional but recommended for cron/home delivery; defaults to the allowed user target in the current single-user deployment
- `hermes_parallel_api_key`
- `hermes_firecrawl_api_key`
- `hermes_browserbase_api_key`
- `hermes_browserbase_project_id`
- `hermes_honcho_api_key`, optional Honcho Cloud API key; required only when `hermes_memory_provider` is explicitly set to `honcho`
- `hermes_1password_service_account_token`
- `copyparty_users`, as a list of account objects with `name` and `password`
- `adguard_admin_password`, as plaintext; the AdGuard role hashes it before writing the service config
- `backup_restic_repository`, the off-host S3-compatible Restic repository URL
- `backup_restic_password`, the Restic repository encryption password
- `backup_aws_access_key_id` and `backup_aws_secret_access_key`, credentials scoped to the backup bucket

Non-secret deployment values:

- `adguard_admin_username`, optional; defaults to `admin`
- `hermes_newrrow_username_ref` and `hermes_newrrow_password_ref`, as 1Password secret references (for example `op://Hermes/Newrrow/username` and `op://Hermes/Newrrow/password`) read by the Hermes Newrrow browser-login plugin at runtime; these are references, not secret values
- `hermes_memory_provider`, empty by default for built-in memory; set to `honcho` only when intentionally enabling Honcho-backed memory
- `hermes_honcho_environment`, defaults to `production` and is rendered as `HONCHO_ENVIRONMENT`

GitHub Actions secret names for the Hermes web, browser, Honcho, and 1Password backends are `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `BROWSERBASE_API_KEY`, `BROWSERBASE_PROJECT_ID`, optional `HONCHO_API_KEY`, and `OP_SERVICE_ACCOUNT_TOKEN`; the CD helper maps them to the matching Ansible variables.

GitHub Actions backup secret names are `BACKUP_RESTIC_REPOSITORY`,
`BACKUP_RESTIC_PASSWORD`, `BACKUP_AWS_ACCESS_KEY_ID`, and
`BACKUP_AWS_SECRET_ACCESS_KEY`.

Do not commit decrypted secret files.

For GitHub Actions, store the Copyparty accounts as `COPYPARTY_USERS_JSON`, for example:

```json
[{"name":"example","password":"replace-me"}]
```
