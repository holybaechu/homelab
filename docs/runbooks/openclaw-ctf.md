# OpenClaw CTF agent and remote Docker sandbox

This deployment has one native `openclaw-gateway.service` on VMID 118. It
hosts the existing agents and the `ctf` agent together; it does not run a
second CTF Gateway, an HTTP relay, relay HMAC credentials, or a second Discord
bot process.

The CTF Docker executor remains separate on VMID 119 (`ctf-executor`). Its
Docker SSH account accepts one source-locked ed25519 key and always executes
`docker system dial-stdio`; it cannot open a shell, forward a port, or choose a
different command. The Gateway has no local Docker Engine or usable local
socket. Its systemd unit makes both `/run/docker.sock` and
`/var/run/docker.sock` inaccessible and reaches the executor only through the
pinned SSH host key and forced command.

## Important one-Gateway limit

Docker's `ssh://` connection helper for the Gateway's `DOCKER_HOST` invokes a
literal `ssh` executable. The Gateway service prepends a root-owned private
SSH shim to `PATH`; that shim accepts only Docker's pinned CTF transport
invocation and reads the key and host pin from its systemd
`CREDENTIALS_DIRECTORY`. OpenClaw does not support giving that process-level
Docker transport or its systemd-loaded credentials to only one agent inside a
shared Gateway process. Therefore the remote Docker capability is process-scoped, not a hard
per-agent credential boundary.

The checked configuration gives only `ctf` a session-scoped Docker sandbox and
its CTF-only tool policy; other agents keep their existing configuration. That
is the normal routing and tool-policy boundary, but it is not equivalent to a
separate Unix service. Do not claim that a host-exec-capable agent could never
reach the capability merely because it is not configured as a Docker sandbox.
A hard credential boundary requires a separate Gateway/process, which this
deployment deliberately does not use.

## Discord routing

One Discord bot logs in directly through the existing Gateway. Its private
`config/openclaw.json` configuration must use the `shared` account and direct
numeric channel bindings. Channel IDs, rather than user, role, or guild
allowlists, select agents. The configuration validation requires:

- The external Discord channel plugin enabled as `plugins.entries.discord`
  and installed as the pinned compatible `@openclaw/discord@2026.7.1` release.
- DMs disabled, group policy `allowlist`, bot messages and config writes off.
- A wildcard Discord guild channel map with enabled numeric channel IDs.
- Exactly one direct Discord binding for each allowed channel; each binding
  names an existing agent and at least one names `ctf`.
- A numeric `commands.ownerAllowFrom` operator list for guarded diagnostics;
  it is separate from and does not weaken channel-only message authorization.
- The `ctf` agent workspace `/var/lib/openclaw/workspaces/ctf`, Docker
  `session` sandbox on `openclaw-ctf`, elevated tools disabled, and the
  approved `message` tool in
  its CTF tool/sandbox policy so it can return bounded ZIP exports directly to
  the requesting Discord channel. Existing agents such as `main` may likewise
  keep their separately configured direct Discord attachment and ZIP handling.

Native Discord file uploads and ZIP replies are approved for both the CTF and
main channels. They remain subject to their channel bindings and each agent's
configured artifact handling; this approval does not enable DMs, user/role
allowlists, or cross-channel routing. Keep both agents' `message.crossContext`
flags false so a tool invocation cannot select a different context or provider.

No Discord guild ID is needed for this authorization model. Create as many
campaign channels as wanted, add each numeric channel ID to the same shared
account map, and bind it to the intended agent. A channel name is not an
authorization value and can change without redeploying.

## Production secrets and deployment

GitHub Actions needs only these OpenClaw secrets:

- `OPENCLAW_GATEWAY_TOKEN`: exactly 64 hexadecimal characters.
- `OPENCLAW_DISCORD_BOT_TOKEN`: the token for the one shared Discord bot.

The deployment writes both to root-owned files under `/etc/openclaw/secrets`
and passes them to the Gateway using systemd credentials. There is no
`OPENCLAW_CTF_GATEWAY_TOKEN`, CTF OpenAI API key, Discord relay HMAC, or
separate CTF bot token.

The CTF executor SSH key is generated root-only on the Gateway host at
`/etc/openclaw/secrets/ctf_docker_client_key`; the transport role installs only
its public half in the executor's forced-command account and pins the
executor's public host key at `/etc/openclaw/trust/ctf_docker_known_hosts`. The
root-owned trust file is group-readable by the Gateway, while the private
client key remains a systemd credential. Do not
copy either private credential into the private OpenClaw checkout or GitHub
secrets.

Before an authorized production deployment:

1. Add the two GitHub repository secrets above.
2. Update the private `config/openclaw.json` channel map/bindings with the
   desired numeric Discord channels and the `ctf` agent contract above.
3. Dispatch the approved production deployment workflow. Its ordering creates
   the executor, provisions the one Gateway, then installs the forced SSH
   transport before continuing with the other services.

After deployment, the validation playbook checks the no-local-socket rule, the
single active Gateway, disabled retired split service units, the forced remote
Docker transport, CTF workspace mounts, and direct Discord credential wiring.
