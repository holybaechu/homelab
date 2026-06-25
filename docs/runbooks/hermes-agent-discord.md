# Hermes Agent Discord gateway

Hermes Agent runs in the `hermes` Debian LXC as a systemd-managed Discord gateway. The gateway is outbound-only: there is no Caddy route or browser WebUI for Hermes.

## Secrets

Set these GitHub Actions secrets in the `prod` environment:

- `HERMES_DISCORD_BOT_TOKEN`: Discord bot token from the Discord Developer Portal.
- `HERMES_DISCORD_ALLOWED_USERS`: comma-separated Discord user IDs allowed to use the bot.
- `PARALLEL_API_KEY`: Parallel API key used by Hermes `web_search`.
- `FIRECRAWL_API_KEY`: Firecrawl API key used by Hermes `web_extract`.
- `BROWSERBASE_API_KEY`: Browserbase API key used by Hermes browser automation.
- `BROWSERBASE_PROJECT_ID`: Browserbase project ID used by Hermes browser automation.
- `OP_SERVICE_ACCOUNT_TOKEN`: 1Password service account token used by the `op` CLI for non-interactive secret access.

The CD workflow writes them to Ansible as `hermes_discord_bot_token`, `hermes_discord_allowed_users`, `hermes_parallel_api_key`, `hermes_firecrawl_api_key`, `hermes_browserbase_api_key`, `hermes_browserbase_project_id`, and `hermes_1password_service_account_token`. Ansible renders Hermes' expected runtime names into `/etc/hermes-gateway.env`:

- `DISCORD_BOT_TOKEN`
- `DISCORD_ALLOWED_USERS`
- `PARALLEL_API_KEY`
- `FIRECRAWL_API_KEY`
- `BROWSERBASE_API_KEY`
- `BROWSERBASE_PROJECT_ID`
- `BROWSERBASE_PROXIES`
- `BROWSERBASE_ADVANCED_STEALTH`
- `AGENT_BROWSER_ARGS` (set to `--no-sandbox,--disable-dev-shm-usage` for the local Chrome sidecar inside the unprivileged LXC)
- `OP_SERVICE_ACCOUNT_TOKEN`
- `HOME` (set to `/var/lib/hermes` for `agent-browser`'s local browser cache)

## Web search

This repo configures Hermes web tooling as:

```yaml
web:
  search_backend: parallel
  extract_backend: firecrawl
```

That gives Hermes Parallel-backed search results and Firecrawl-backed page extraction/scraping. The runtime config helper preserves the existing model/provider settings while enforcing these web backend settings.

## Browser automation

Hermes browser automation is enabled for the Discord gateway with Browserbase cloud browsing:

```yaml
browser:
  cloud_provider: browserbase
  auto_local_for_private_urls: true
platform_toolsets:
  discord:
    - hermes-discord
    - browser
```

The Ansible role installs Node.js/npm and the `agent-browser` package from the pinned Hermes Agent checkout. Browserbase hosts Chromium for public cloud sessions. Because `auto_local_for_private_urls: true` keeps LAN/localhost URLs out of Browserbase, the role also installs a local browser runtime under `/var/lib/hermes/.agent-browser/browsers` with `HOME=/var/lib/hermes` so Hermes' local sidecar can handle private targets.

## 1Password secrets

Hermes can use 1Password-managed secrets from Discord through its terminal tools. The Ansible role installs the official 1Password CLI (`op`) from the 1Password Debian apt repository, installs the Hermes optional skill `official/security/1password` into `/var/lib/hermes/skills/security/1password`, and exposes `OP_SERVICE_ACCOUNT_TOKEN` to the gateway service. Use the service-account flow rather than desktop-app sign-in for the headless LXC.

Examples Hermes can run after deploy:

```bash
op read "op://Vault/Item/field"
op run -- env | grep '^EXAMPLE_'
```

Do not ask Hermes to print raw secret values unless you explicitly need to reveal one; prefer `op run` and `op inject` for commands and templates. Scope the 1Password service account narrowly to the vaults/items Hermes is allowed to read, because allowed Discord users can ask Hermes to run terminal commands that use this token. The role keeps `/var/lib/hermes/.config/op` owned by the `hermes` service user and keeps `/var/lib/hermes/.config/op/config` at mode `0600`, because the 1Password CLI refuses to read that config file when its permissions are broader.

## Newrrow points automation

The role installs a local Hermes skill at `/var/lib/hermes/skills/productivity/newrrow-points-automation`. It migrates the packaged Newrrow point checklist workflow to Hermes browser automation/`agent-browser` and keeps the detailed UI route/checklist reference under the skill's `references/ui-flow.md`.

Newrrow login uses 1Password secret references instead of live browser password-manager state. The tracked inventory configures:

```yaml
hermes_newrrow_base_url: https://gbsm.newrrow.com
hermes_newrrow_home_url: https://gbsm.newrrow.com/csr-platform/home
hermes_newrrow_username_ref: op://Homelab/Newrrow/username
hermes_newrrow_password_ref: op://Homelab/Newrrow/password
```

Ansible renders those into `/etc/hermes-gateway.env` as `NEWRROW_BASE_URL`, `NEWRROW_HOME_URL`, `NEWRROW_LOGIN_URL`, `NEWRROW_USERNAME_REF`, and `NEWRROW_PASSWORD_REF`. Ensure the Hermes 1Password service account can read the referenced item fields, then ask Hermes to use `$newrrow-points-automation`.

If Newrrow presents a login page, the skill directs Hermes to run:

```bash
bash "$HERMES_HOME/skills/productivity/newrrow-points-automation/scripts/newrrow-login.sh"
```

The helper reads the configured `op://` refs with `op read`, sends the password only through `agent-browser auth save --password-stdin`, runs `agent-browser auth login`, and deletes the temporary auth profile. Do not print the Newrrow username/password values in Discord or logs.

## Context compression

The runtime config helper also enforces Hermes' global context-compression behavior:

```yaml
compression:
  threshold: 0.85
  codex_gpt55_autoraise: false
auxiliary:
  compression:
    provider: main
    model: ""
    timeout: 300
```

This keeps the general compaction trigger at 85%, disables the Codex gpt-5.5 route-specific autoraise override, and raises the compression-summary call timeout above the previous 120s live setting that produced `Codex auxiliary Responses stream exceeded 120.0s total timeout`. The summary provider remains `main`, so compression continues to use the configured main Hermes model route with a longer budget instead of an operator-only live edit.

## Discord setup

1. Create a Discord application and bot in the Discord Developer Portal.
2. Enable Server Members Intent and Message Content Intent.
3. Invite the bot with the `bot` and `applications.commands` scopes.
4. Grant at least View Channels, Send Messages, Read Message History, and Attach Files.
5. Copy your Discord user ID with Developer Mode enabled and add it to `HERMES_DISCORD_ALLOWED_USERS`.

With the default repo settings, DMs always receive responses. Server channels require an explicit bot mention, and Hermes ignores messages that mention other users but not the bot.

## Deploy and validate

1. Confirm the Discord, web backend, Browserbase, and 1Password secrets exist in the `prod` environment.
2. Run `infra/ansible/playbooks/bootstrap.yml` through CD, or directly if doing a controlled maintenance deploy.
3. Run `infra/ansible/playbooks/site.yml`.
4. Run `infra/ansible/playbooks/validate.yml`.
5. Confirm `systemctl is-active hermes-gateway` returns `active` on the Hermes LXC.
6. DM the bot or mention it in an allowed server channel.

## Persistent paths

- `/var/lib/homelab/hermes/home` is mounted inside the LXC as `/var/lib/hermes`.
- `/var/lib/homelab/hermes/workspace` is mounted inside the LXC as `/workspace`.

The service does not mount unrelated homelab datasets.
