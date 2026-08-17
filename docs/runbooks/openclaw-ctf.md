# OpenClaw CTF agent and local Docker sandbox

This deployment has one native `openclaw-gateway.service` on VMID 118. It
hosts the existing agents and the `ctf` agent together; it does not run a
second CTF Gateway, an HTTP relay, relay HMAC credentials, or a second Discord
bot process.

The Gateway LXC runs its own Docker Engine and the pinned Kali sandbox image.
The Gateway service joins the local `docker` group and OpenClaw creates one
session-scoped CTF container per session on that same host.

## Important one-Gateway limit

OpenClaw does not support giving the local Docker socket to only one agent
inside a shared Gateway process. The Gateway process therefore has a
process-scoped Docker capability, while the checked agent configuration routes
only `ctf` into the Kali sandbox.

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
- Every CTF channel requires an explicit bot mention. Each mentioned parent
  message automatically creates a 24-hour Discord thread, and the thread is a
  separate OpenClaw session without inherited parent-channel transcript.
- Discord thread actions and thread bindings are enabled so native/subagent
  session spawns stay attached to their thread.
- The bot role needs View Channel, Send Messages, Create Public Threads, Send
  Messages in Threads, Read Message History, and Attach Files in each CTF
  channel. Message Content intent remains required.
- Exactly one direct Discord binding for each allowed channel; each binding
  names an existing agent and at least one names `ctf`.
- A numeric `commands.ownerAllowFrom` operator list for guarded diagnostics;
  it is separate from and does not weaken channel-only message authorization.
- The `ctf` agent workspace `/var/lib/openclaw/workspaces/ctf`, Docker
  `session` sandbox on `openclaw-ctf`, a writable root filesystem, container
  UID/GID `0:0`, default Docker capabilities minus `AUDIT_WRITE` and `MKNOD`,
  no configured CPU/memory/PID/ulimit caps, elevated OpenClaw tools disabled,
  and the approved `message` tool in
  its CTF tool/sandbox policy so it can return bounded ZIP exports directly to
  the requesting Discord channel. Existing agents such as `main` may likewise
  keep their separately configured direct Discord attachment and ZIP handling.
- The Codex plugin enables its sandbox exec-server. This maps Codex-native
  shell and file operations into the active CTF Docker environment instead of
  executing them as the `openclaw` service user on the Gateway host. Without
  this switch, merely exposing the deferred `sandbox_exec` tool is insufficient:
  Codex can still choose its native `bash` surface.
- The Kali image includes pinned `uv`. The CTF prompt allows agents to install
  packages with `apt`, `uv`, or other methods available inside the sandbox.
  Installations remain session-local, while APT archives, the uv cache, and
  Camoufox downloads persist under the CTF workspace `.cache` tree.
- The Kali image also preinstalls Camoufox 0.5.4, Xvfb,
  Chromium/Chromedriver, `ffuf`, `gobuster`,
  `hashcat`, `john`, `sqlmap`, and `yara`. The CTF agent may use the selected
  runtime's native image and PDF analysis, FTS-only local
  memory search, and Exa neural/keyword search through the pinned official
  provider plugin. CTF subagent count, depth, and timeout fields are omitted so
  the pinned OpenClaw defaults apply.

Camoufox running with `headless="virtual"` on Xvfb is the primary anti-detect
automation path; Chromium and Chromedriver remain the compatibility fallback.
Neither path guarantees that a site cannot detect automation.

Native Discord file uploads and ZIP replies are approved for both the CTF and
main channels. They remain subject to their channel bindings and each agent's
configured artifact handling; this approval does not enable DMs, user/role
allowlists, or cross-channel routing. Keep both agents' `message.crossContext`
flags false so a tool invocation cannot select a different context or provider.

No Discord guild ID is needed for this authorization model. Create as many
campaign channels as wanted, add each numeric channel ID to the same shared
account map, and bind it to the intended agent. A channel name is not an
authorization value and can change without redeploying.

The Codex plugin uses its default app-server instructions. Deployment removes
the retired CTF `model_instructions_file` override and does not manage a
separate CTF Codex home.

## Production secrets and deployment

GitHub Actions needs only these OpenClaw secrets:

- `OPENCLAW_GATEWAY_TOKEN`: exactly 64 hexadecimal characters.
- `OPENCLAW_DISCORD_BOT_TOKEN`: the token for the one shared Discord bot.
- `OPENCLAW_EXA_API_KEY`: the Exa API key for managed CTF web search.

The deployment writes all three to root-owned files under `/etc/openclaw/secrets`
and passes them to the Gateway using systemd credentials. There is no
`OPENCLAW_CTF_GATEWAY_TOKEN`, CTF OpenAI API key, Discord relay HMAC, or
separate CTF bot token.

Before an authorized production deployment:

1. Add the three GitHub repository secrets above.
2. Update the private `config/openclaw.json` channel map/bindings with the
   desired numeric Discord channels and the `ctf` agent contract above.
3. Dispatch the approved production deployment workflow. It installs local
   Docker, builds the pinned Kali image, and activates the one Gateway.

After deployment, the validation playbook checks the local Docker socket and
pinned image, single active Gateway, disabled retired split service units, CTF
workspace mounts, and direct Discord credential wiring.
