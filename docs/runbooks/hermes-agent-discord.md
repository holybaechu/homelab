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

- `HERMES_CONFIG_REPO_TOKEN`: fine-scoped GitHub token that can read and push the private `holybaechu/hermes-config` repo.
- `HERMES_CONFIG_WEBHOOK_SECRET`: shared HMAC secret for the hermes-config GitHub push webhook receiver.

The CD workflow writes them to Ansible as `hermes_discord_bot_token`, `hermes_discord_allowed_users`, `hermes_parallel_api_key`, `hermes_firecrawl_api_key`, `hermes_browserbase_api_key`, `hermes_browserbase_project_id`, `hermes_1password_service_account_token`, `hermes_config_repo_token`, and `hermes_config_webhook_secret`. Ansible renders Hermes' expected runtime names into `/etc/hermes-gateway.env`:

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

`homelab` is the canonical owner for runtime/environment-derived config keys: `web.*`, `browser.*`, `compression.*`, `auxiliary.compression.*`, `plugins.enabled`, and `platform_toolsets.discord`. The live `hermes-config/config/default/config.yaml` must not set those keys; the apply helper rejects them so a remote live-config push cannot silently override IaC-owned runtime values. To change one of those values, update homelab inventory/templates and redeploy.

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
    - kanban
```

The Ansible role installs Node.js/npm and the `agent-browser` package from the pinned Hermes Agent checkout. Browserbase hosts Chromium for public cloud sessions. Because `auto_local_for_private_urls: true` keeps LAN/localhost URLs out of Browserbase, the role also installs a local browser runtime under `/var/lib/hermes/.agent-browser/browsers` with `HOME=/var/lib/hermes` so Hermes' local sidecar can handle private targets.

## 1Password secrets

Hermes can use 1Password-managed secrets from Discord through its terminal tools. The Ansible role installs the official 1Password CLI (`op`) from the 1Password Debian apt repository and exposes `OP_SERVICE_ACCOUNT_TOKEN` to the gateway service. The Hermes `1password` skill itself is owned by the live `holybaechu/hermes-config` checkout at `/var/lib/hermes/skills/security/1password`; homelab validates that file exists and fails the deploy if the live config repo removed it instead of reinstalling it into the symlinked skills tree.

Examples Hermes can run after deploy:

```bash
op read "op://Vault/Item/field"
op run -- env | grep '^EXAMPLE_'
```

Do not ask Hermes to print raw secret values unless you explicitly need to reveal one; prefer `op run` and `op inject` for commands and templates. Scope the 1Password service account narrowly to the vaults/items Hermes is allowed to read, because allowed Discord users can ask Hermes to run terminal commands that use this token. The role keeps `/var/lib/hermes/.config/op` owned by the `hermes` service user and keeps `/var/lib/hermes/.config/op/config` at mode `0600`, because the 1Password CLI refuses to read that config file when its permissions are broader.

## Newrrow points automation

The Hermes behavior artifacts now come from the private `holybaechu/hermes-config` repository instead of being copied from homelab role files. The live deployment keeps:

```text
/var/lib/hermes/hermes-config   # git checkout of holybaechu/hermes-config main
/var/lib/hermes/skills          -> /var/lib/hermes/hermes-config/skills
/var/lib/hermes/memories        -> /var/lib/hermes/hermes-config/memories
/var/lib/hermes/plugins         -> /var/lib/hermes/hermes-config/plugins
```

The Ansible role installs `/opt/hermes/hermes-config-git-sync`, `/opt/hermes/hermes-config-apply`, a local inotify watch service, a GitHub webhook receiver, and a reconciliation timer. The sync script never uses `git reset --hard`; it commits local Hermes auto-improvements first, fetches/rebases or fast-forwards `main`, pushes local-only changes, and then runs the apply helper. Sync commits are authored as `holybaechu <holybaechu@proton.me>` so the live learned-state history stays under the maintainer account. If changed files match runtime-impacting prefixes such as `config/`, `profiles/`, `plugins/`, `rules/`, `kanban/`, or `cron/`, the sync script's restart handler runs `systemctl try-restart hermes-gateway.service`. Skills and memories sync immediately on disk, while current conversations may still need `/reload-skills`, `/reset`, or a new session to see them.

The Newrrow skill lives at `/var/lib/hermes/skills/newrrow-points-automation` through that symlinked checkout, and the trusted plugin lives at `/var/lib/hermes/plugins/newrrow-browser-login`. It migrates the packaged Newrrow point checklist workflow to Hermes `browser_*` tools so public Newrrow URLs use the configured Browserbase browser path, while the local browser runtime remains only for private/LAN auto-local routing. The detailed UI route/checklist reference lives under the skill's `references/ui-flow.md`.

Newrrow login uses 1Password secret references instead of live browser password-manager state. The tracked inventory configures:

```yaml
hermes_newrrow_username_ref: op://Hermes/Newrrow/username
hermes_newrrow_password_ref: op://Hermes/Newrrow/password
```

Ansible renders those into `/etc/hermes-gateway.env` as `NEWRROW_USERNAME_REF` and `NEWRROW_PASSWORD_REF`. The Newrrow URL is hardcoded in the skill/plugin as `https://gbsm.newrrow.com/csr-platform/home` instead of being exposed as gateway environment. Ensure the Hermes 1Password service account can read the referenced item fields, then ask Hermes to use `$newrrow-points-automation`.

The runtime flow is:

1. Use `browser_navigate` on the public Newrrow home URL.
2. If the snapshot shows a login page, call the plugin tool `newrrow_browser_login`. The plugin reads the configured `op://` refs with `op read` inside the Hermes process, injects credentials into the active Browserbase/CDP-backed browser-tool session, and returns only non-secret status.
3. Continue all Newrrow UI work with Hermes `browser_*` tools (`browser_click`, `browser_type`, `browser_scroll`, `browser_press`, `browser_snapshot`).

Do not type raw Newrrow passwords through `browser_type`, because its arguments/results are model-visible. Do not use bare `agent-browser open`, `agent-browser auth save`, or `agent-browser auth login` for Newrrow runtime operation; that bypasses Hermes browser routing and can silently use local Chromium for this public URL. `scripts/newrrow-login.sh` is retained only as a deployment/debugging credential preflight that verifies the 1Password refs and prints `newrrow_1password_refs_ready`.

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


## Multi-profile Kanban fleet

The default `/var/lib/hermes` profile remains the Discord gateway and the single gateway-embedded Kanban dispatcher. IaC also creates a homelab-managed **5+1** profile fleet under `/var/lib/hermes/profiles`:

| Profile | Role |
| --- | --- |
| `orchestrator` | +1 Kanban intake/orchestration profile; decomposes broad goals, links cards, assigns specialist workers, and avoids implementation work. |
| `homelab` | Production homelab IaC/ops: `holybaechu/homelab`, OpenTofu, Ansible, Proxmox LXC, GitHub Actions, deployment validation. |
| `dev` | General coding, debugging, tests, refactors, docs, and PR work outside production homelab ops. |
| `research` | Web/docs/paper discovery, source-backed summaries, technical comparisons, monitoring, written briefs. |
| `sandbox` | Low-trust experiments, unfamiliar repos, dependency spikes, build trials, and throwaway scripts. |
| `browser-protected` | Protected public web automation that should use Browserbase residential proxy mode. |

Kanban is the communication layer between profiles. The default gateway config owns dispatching:

```yaml
platform_toolsets:
  discord:
    - hermes-discord
    - browser
    - kanban
kanban:
  dispatch_in_gateway: true
  orchestrator_profile: orchestrator
  default_assignee: orchestrator
  max_spawn: 3
  max_in_progress: 3
  max_in_progress_per_profile: 1
```

Every named profile gets `kanban.dispatch_in_gateway: false` so accidentally starting a per-profile gateway does not create a second dispatcher racing on the same board. Worker profiles receive task-scoped `kanban_*` tools when spawned by the dispatcher; the `orchestrator` profile also has the `kanban` toolset on CLI so it can create/link/comment cards. IaC runs `hermes kanban init`, creating `/var/lib/hermes/kanban.db` on the persistent home mount.

Browserbase proxy policy is profile-scoped through each profile's `.env`: default/normal profiles keep `BROWSERBASE_PROXIES=false`, while `browser-protected` sets `BROWSERBASE_PROXIES=true` for sites that actually need residential proxy mode. API keys still come from the gateway service environment; profile `.env` files contain only non-secret runtime overrides.

Useful checks after deploy:

```bash
runuser -u hermes -- env HERMES_HOME=/var/lib/hermes HOME=/var/lib/hermes /opt/hermes/venv/bin/hermes profile list
runuser -u hermes -- env HERMES_HOME=/var/lib/hermes HOME=/var/lib/hermes /opt/hermes/venv/bin/hermes kanban list
```

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
5. Confirm `systemctl is-active hermes-gateway`, `systemctl is-active hermes-config-watch`, `systemctl is-active hermes-config-webhook`, and `systemctl is-active hermes-config-sync.timer` return active on the Hermes LXC.
6. Confirm `/var/lib/hermes/skills`, `/var/lib/hermes/memories`, and `/var/lib/hermes/plugins` resolve into `/var/lib/hermes/hermes-config`.
7. DM the bot or mention it in an allowed server channel.

## Persistent paths

- `/var/lib/homelab/hermes/home` is mounted inside the LXC as `/var/lib/hermes`.
- `/var/lib/homelab/hermes/workspace` is mounted inside the LXC as `/workspace`.

The service does not mount unrelated homelab datasets.
