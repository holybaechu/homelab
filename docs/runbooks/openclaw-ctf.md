# Isolated OpenClaw CTF Agent

The permanent `ctf` agent is defined in the private `openclaw-setup`
repository. It keeps the existing `main` agent as the default and uses
temporary session-scoped `ctf` subagents for parallel work. The public
deployment owns the execution boundary; the private repository owns the agent
instructions, active config, artifact scripts, and Discord binding template.

## Execution boundary

VMID 118 runs the native Gateway. It has a Docker CLI but no Docker Engine,
`/var/run/docker.sock`, nesting, or TUN. VMID 119 (`ctf-executor`) runs the
Docker Engine and receives only two CTF-scoped paths:

```text
/var/lib/homelab/openclaw-ctf (Proxmox)
  -> /srv/openclaw-ctf (Gateway and executor LXCs)
  -> /workspace (one Kali sandbox session)

/var/lib/homelab/openclaw-ctf-sandbox-skills (Proxmox)
  -> /var/lib/openclaw/sandbox/skills-workspaces (Gateway and executor LXCs)
  -> /workspace/.openclaw/sandbox-skills/skills (read-only in one Kali session)
```

The second path is not challenge data or general Gateway state. OpenClaw
generates filtered skill copies there for each sandbox session, and its remote
Docker backend resolves bind sources on the executor. Sharing only that subtree
keeps container registry metadata and the rest of the Gateway state local.

The Gateway connects with `DOCKER_HOST=ssh://openclaw-ctf-docker@192.168.0.6`.
Its systemd service receives an SSH private key and a pinned executor host key
only as systemd credentials. The executor permits that key from
`192.168.0.5` only, forces `docker system dial-stdio`, and denies interactive
shells, TTYs, forwarding, passwords, and X11. This Docker API capability is
therefore confined to a dedicated LXC with no unrelated application data.

The custom `openclaw-ctf` Docker network is the only network used by CTF
sandboxes. It disables inter-container communication and blocks traffic from
the sandbox subnet to RFC1918, Tailscale, loopback, and link-local IPv4
networks. It intentionally permits public egress for web/OSINT challenges.
Kali containers run as UID/GID 1000 with a read-only root filesystem, tmpfs
scratch paths, all Linux capabilities dropped, and no Docker socket mount.

## Private agent and artifact workflow

Ansible copies the tracked private CTF bootstrap from
`workspaces/ctf/` to `/srv/openclaw-ctf` without copying challenge data back to
Git. Every new challenge must be initialized with
`scripts/init-ctf-challenge.py`; it preserves inbound attachments under
`challenges/<slug>/files/`, creates `work/` and `evidence/screenshots/`, and
seeds `writeup.md`. The parent agent updates the write-up continuously and
uses `scripts/export-ctf-package.py` to generate a ZIP below `exports/`.
Only the parent sends that ZIP through the CTF Discord channel.

The tracked agent contract prevents use of elevated execution, gateway tools,
SSH credentials, host paths, and a Docker socket. The OpenClaw config also
denies gateway, cron, browser, canvas, and node tools to the CTF sandbox.

## Shared Discord bot routing

The committed active config intentionally has no Discord account because no
bot token or Discord IDs belong in Git. One bot account can serve multiple
agents and multiple campaign channels per agent. Each numeric Discord channel
ID is both an allowlist key and an exact routing target; the CTF route is one
such binding.

After the CTF bridge/service split is deployed:

1. Copy the structure in private
   `config/discord.example.json` into the active private config. Do not add
   Discord user IDs, channel names/slugs, or guild IDs. For every campaign
   channel, add its numeric channel ID once under `guilds["*"].channels` and
   once as an exact binding's `match.peer.id`. Several bindings may use the
   same `agentId`; other agents use the same pattern. All bindings use the
   shared Discord account.
2. Keep `dmPolicy: disabled`, `groupPolicy: allowlist`, `configWrites: false`,
   and `allowBots: false`. Do not give the bot permissions outside explicitly
   allowlisted agent channels. Discord channel/category permissions are the
   human-access allowlist: anyone able to post in an allowed channel can reach
   that channel's bound agent.
3. Set the GitHub `prod` secret `OPENCLAW_DISCORD_BOT_TOKEN` and the
   `OPENCLAW_DISCORD_ENABLED=true` environment variable, then deploy the
   full Ansible site play. The token is written as a root-only systemd
   credential source and is never committed.
4. Send a message from a permitted Discord member in every configured channel.
   It must reach that channel's bound agent. A DM, unlisted channel, or bot
   message must not reach an agent. Verify the CTF agent keeps campaign
   artifacts namespaced by campaign/channel because its mutable workspace is
   shared by its campaign bindings.

Do not activate the CTF route before that split. The current single Gateway
process cannot isolate its remote Docker transport from unrelated agents.
Other shared-bot channels can use the generic account only after their own
routing and authorization checks pass.

## Verification

`validate.yml` verifies the executor image and tools, read-only/non-root
sandbox flags, custom network and egress firewall, restricted SSH account, and
the Gateway's systemd-credential remote Docker connection. It also creates a
temporary generated-skill fixture and proves that the remote Kali container
can read it through the same mount path OpenClaw uses. The Gateway itself
still has no local Docker service or socket. Private repository tests exercise
challenge initialization and ZIP exports; the pinned OpenClaw CLI validates the
active schema and audits secrets before activation.
