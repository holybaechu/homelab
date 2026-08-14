# Isolated OpenClaw CTF Agent

The permanent CTF agent and its workspace contract live in the private
openclaw-setup repository. This repository deploys the security boundary: two
separate OpenClaw Gateway services and one small Discord relay. The split keeps
the existing main agent available without giving it CTF Docker access.

## Service split

    Discord Gateway + REST (one bot token)
                     |
                     v
    openclaw-discord-relay (UID/GID 1002)
        | fixed HMAC-authenticated loopback requests
        +--> core Gateway, 127.0.0.1:18789 (main and ordinary agents)
        \--> CTF Gateway,  127.0.0.1:19789 (ctf only)
                                          |
                                          \--> remote Docker executor, VMID 119

The core openclaw-gateway.service stays on port 18789. It has no DOCKER_HOST,
Docker client, Docker SSH key, CTF workspace, CTF state, or Discord bot token.
Its service account cannot read the CTF service's paths.

The separate openclaw-ctf-gateway.service listens only on 127.0.0.1:19789. It
runs as openclaw-ctf (UID/GID 1001) with a separate config, state directory,
auth directory, workspace, Gateway bearer token, and relay HMAC. It is the
only Gateway service that receives the CTF-scoped Docker client and pinned SSH
transport credential. The two Gateway bearer tokens are independent and must
not be reused. Its model provider receives a separate
OPENCLAW_CTF_OPENAI_API_KEY systemd credential; the core Gateway and Discord
relay cannot read it.

openclaw-discord-relay.service runs as a third identity
(openclaw-discord-relay, UID/GID 1002). It is the *only* process that logs in
to Discord or holds OPENCLAW_DISCORD_BOT_TOKEN. It has neither Gateway bearer
token, neither Docker credential, nor access to /srv/openclaw-ctf. It can call
only the fixed core and CTF loopback relay endpoints, authenticated with
different per-target HMAC secrets.

## CTF execution boundary

VMID 118 hosts both local Gateway services but has no Docker Engine, Docker
socket, nesting, or TUN. The CTF Docker client is installed only below the CTF
service's private path. VMID 119 (ctf-executor) hosts the Docker Engine and
receives only CTF-scoped bind mounts:

    /var/lib/homelab/openclaw-ctf (Proxmox)
      -> /srv/openclaw-ctf (CTF Gateway and executor LXCs)
      -> /workspace (one Kali sandbox session)

    /var/lib/homelab/openclaw-ctf-sandbox-skills (Proxmox)
      -> /var/lib/openclaw-ctf/sandbox/skills-workspaces (CTF Gateway and executor LXCs)
      -> /workspace/.openclaw/sandbox-skills/skills (read-only in one Kali session)

The second path contains filtered, generated skill copies rather than challenge
data or general Gateway state. The remote Docker backend resolves its sources
on the executor, so only this subtree is shared.

The CTF Gateway connects using
DOCKER_HOST=ssh://openclaw-ctf-docker@192.168.0.6. Its systemd unit receives
the SSH private key and pinned executor host key only as systemd credentials.
The executor allows that key only from 192.168.0.5, forces
docker system dial-stdio, and denies shells, TTYs, forwarding, passwords, and
X11. Kali containers run as the CTF UID/GID (1001:1001), have a read-only root
filesystem and tmpfs scratch paths, drop all Linux capabilities, and never
receive a Docker socket.

The custom openclaw-ctf Docker network is the only network used by CTF
sandboxes. It disables inter-container communication and blocks access to
RFC1918, Tailscale, loopback, and link-local IPv4 networks while permitting
public egress needed for web and OSINT challenges.

## Channel-only Discord routing

The private route file is config/discord-relay-routes.json. It is the only
Discord authorization and routing input for the relay:

    {
      "version": 1,
      "routes": {
        "1537716407889039391": {"target": "ctf"},
        "1537716991929098301": {"target": "core"}
      }
    }

Each key is an exact numeric Discord channel ID. A channel ID is globally
unique, so do not configure a channel name, guild ID, category ID, or Discord
user allowlist. The relay uses a guild event only to reject DMs; it never uses
the guild as a routing wildcard. It also ignores bot and webhook messages and
drops every unlisted channel.

Discord channel and category permissions are the human-access layer: anyone
who can post in an explicitly routed channel can reach only that channel's
target. To add another campaign channel for the CTF agent, add one more numeric
entry with "target": "ctf"; any number of channels may select the same target.
This does not grant access to other channels or agents.

All CTF-routed channels share one mutable CTF workspace. The route map isolates
ingress, per-channel sessions, and replies, but it is not a hard
cross-campaign data boundary. Route only channels whose members are permitted
to share challenge files and exports.

The current relay deliberately recognizes only the fixed core and ctf targets.
Adding another independently privileged agent requires a reviewed target
service/plugin endpoint; do not place an arbitrary agent ID in the route file
and assume it is authorized.

One bot can therefore serve both the core and CTF routes. Do not create or
store per-agent bot tokens. In the Discord Developer Portal, enable the Message
Content privileged intent and grant the bot only the channel permissions it
needs (at minimum view channel, send messages, and attach files in routed
channels).

## Approved CTF attachment and ZIP handoff

The CTF route accepts attachments only from an explicitly routed CTF channel.
The relay passes at most ten pieces of bounded attachment metadata to the CTF
endpoint; it does not download, retain, or gain filesystem access to challenge
data. The CTF plugin independently revalidates the Discord CDN URL, downloads
within its private byte limit, and atomically stages files only under
`/srv/openclaw-ctf/media/inbound/<requestId>/`. The request ID is a 32-character
lowercase hexadecimal value bound to the originating Discord message.

CTF output is opt-in and request-correlated. The CTF agent must use the
plugin-provided `ctf_publish` tool, which accepts only a filename and binds it
to the active trusted request. The plugin may return only the regular,
non-symlink file `exports/<requestId>/<filename>.zip`. The relay independently accepts one
safe-named ZIP with a ZIP signature and a decoded size of at most 25 MiB, then
uploads it only to that same originating channel. It does not accept arbitrary
paths, files, targets, or Discord mentions from a plugin response.

The CTF endpoint has a bounded ten-minute relay request window, and relay work
is serialized per channel. This supports a normal CTF analysis turn without
allowing an unbounded queue or transfer. A failed plugin request produces only
a generic reply in the original channel; it never reroutes the message or
falls back to the core agent.

## Deployment and activation

1. Keep the private CTF config, plugins, workspace bootstrap, and
   discord-relay-routes.json in the private openclaw-setup checkout. Do not
   copy Discord tokens, route files, or CTF data into this repository.
2. Add independent GitHub prod secrets OPENCLAW_GATEWAY_TOKEN,
   OPENCLAW_CTF_GATEWAY_TOKEN, and OPENCLAW_CTF_OPENAI_API_KEY. All three are
   required for the full CTF deployment, including when Discord remains
   disabled. Add the single generic OPENCLAW_DISCORD_BOT_TOKEN only when
   preparing to activate the relay.
3. Deploy the split with OPENCLAW_DISCORD_ENABLED=false (or leave the variable
   unset). This installs and keeps the relay stopped; it does not log the bot
   in to Discord.
4. Run the private CTF relay tests, validate the numeric route map and Discord
   permissions, then exercise an attachment and request-correlated ZIP in a
   non-production routed channel. Only then set OPENCLAW_DISCORD_ENABLED=true
   and run the full production deployment.

Ansible keeps the bot token and both relay HMACs out of Git as root-owned
systemd credential sources. The relay HMACs are generated on the target and
are not GitHub secrets. No service receives another service's bearer token.

## Verification

validate.yml verifies the executor image and tools, non-root/read-only sandbox
flags, network policy, restricted SSH transport, and the CTF service's remote
Docker connection. It also verifies that the core service has no CTF Docker
capability or CTF workspace access and that the relay has no Gateway, Docker,
or CTF-data access. The relay role self-checks its safe ZIP response contract
before enabling Discord. Private-repository tests cover attachment staging and
request-correlated ZIP creation; they complement, but do not replace, the
non-production Discord end-to-end check.
