# Hermes Agent Discord gateway

Hermes Agent runs in the `hermes` Debian LXC as a systemd-managed Discord gateway. The gateway is outbound-only: there is no Caddy route or browser WebUI for Hermes.

## Secrets

Set these GitHub Actions secrets in the `prod` environment:

- `HERMES_DISCORD_BOT_TOKEN`: Discord bot token from the Discord Developer Portal.
- `HERMES_DISCORD_ALLOWED_USERS`: comma-separated Discord user IDs allowed to use the bot.

The CD workflow writes them to Ansible as `hermes_discord_bot_token` and `hermes_discord_allowed_users`. Ansible renders Hermes' expected runtime names, `DISCORD_BOT_TOKEN` and `DISCORD_ALLOWED_USERS`, into `/etc/hermes-gateway.env`.

Provider/model API keys are not deployed by this repo. Complete provider/model setup from the Hermes CLI after the service is running.

## Discord setup

1. Create a Discord application and bot in the Discord Developer Portal.
2. Enable Server Members Intent and Message Content Intent.
3. Invite the bot with the `bot` and `applications.commands` scopes.
4. Grant at least View Channels, Send Messages, Read Message History, and Attach Files.
5. Copy your Discord user ID with Developer Mode enabled and add it to `HERMES_DISCORD_ALLOWED_USERS`.

With the default repo settings, DMs always receive responses. Server channels require an explicit bot mention, and Hermes ignores messages that mention other users but not the bot.

## Deploy and validate

1. Confirm the Discord secrets exist in the `prod` environment.
2. Run `infra/ansible/playbooks/bootstrap.yml` through CD, or directly if doing a controlled maintenance deploy.
3. Run `infra/ansible/playbooks/site.yml`.
4. Run `infra/ansible/playbooks/validate.yml`.
5. Confirm `systemctl is-active hermes-gateway` returns `active` on the Hermes LXC.
6. DM the bot or mention it in an allowed server channel.

## Persistent paths

- `/var/lib/homelab/hermes/home` is mounted inside the LXC as `/var/lib/hermes`.
- `/var/lib/homelab/hermes/workspace` is mounted inside the LXC as `/workspace`.

The service does not mount unrelated homelab datasets.
