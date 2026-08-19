# OpenClaw CTF agent and immutable sandbox

OpenClaw runs one immutable Gateway Compose service on the dedicated OpenClaw
LXC (VMID 118). The same Gateway hosts the configured agents, including `ctf`;
there is no second Gateway, HTTP relay, second Discord bot, or production image
build.

The Gateway release manifest pins two independent OCI artifacts:

- `gateway_ref`: the prevalidated Gateway image at `repository@sha256:...`.
- `ctf_ref`: the prevalidated Kali-based CTF sandbox at
  `repository@sha256:...`.

CI builds and validates both images. Production pulls those exact digests and
activates them with `docker compose --no-build`. The CTF Dockerfile and build
context are never uploaded to the LXC.

## Capability boundary

The Gateway container runs as UID/GID 1000 with a read-only root filesystem,
all Linux capabilities dropped, and only the numeric host Docker group added.
It receives the host Docker socket because OpenClaw creates one CTF container
per configured session. The checked private configuration routes only the
`ctf` agent into that sandbox, but this is a routing boundary rather than a
separate Unix-process credential boundary.

The CTF image runs as UID/GID 1000, matching the Gateway and the owner of the
private `0700` PVE workspace bind mount. CI mounts a real host directory and
proves that this numeric identity can create a mode-0600 file before approving
the image. The image contains the prevalidated analysis tool
set (including pwntools, GDB, nmap, sqlmap, ffuf, gobuster, hashcat, john, yara,
Chromium/Chromedriver, Camoufox, Xvfb, and `uv`). It has no Docker client, host
socket, release credential, or mutable image tag. Package changes therefore
require a CI image build and exact-digest promotion rather than installation on
the production LXC.

## Discord and agent configuration

The exact private config bundle must keep these observable controls:

- One shared Discord account with DMs disabled, allowlisted guild/channel
  bindings, bot messages and config writes disabled.
- Numeric channel IDs and direct agent bindings; channel names are not
  authorization values.
- Explicit bot mentions for CTF parent messages, thread actions enabled, and
  separate per-thread OpenClaw sessions.
- Cross-context message selection disabled and elevated OpenClaw tools disabled.
- The `ctf` workspace and session-scoped Docker sandbox select the exact
  `OPENCLAW_CTF_REF` supplied by the approved release manifest.
- The Codex sandbox exec-server is enabled so shell and file operations execute
  inside the CTF container rather than in the Gateway container.

The Gateway's Discord, Exa, and gateway credentials are root-owned on the host,
group-readable only by the runtime GID, and mounted as individual read-only
files. The autonomous skill-promotion GitHub credential is not exposed to the
Gateway; the separate least-privilege host timer consumes it through a systemd
credential.

## Build and promotion

An OpenClaw image-input change selects `openclaw_gateway` and/or
`openclaw_ctf`. The CI build job:

1. creates the immutable-image build contract;
2. builds and pushes with Buildx;
3. reads the exact digest from Buildx metadata;
4. validates the just-built image and CTF tool/runtime contract; and
5. approves that same-build digest for the source SHA.

The production OpenClaw job uploads only the exact release manifest plus the
deterministic runtime and private-config bundles. It invokes the preinstalled
deployer over the already pinned Tailscale/SSH path. The deployer verifies both
local OCI RepoDigests, starts with `--no-build`, waits for `/readyz`, performs
one authenticated smoke, and records the release only after success.

If activation fails, the deployer brings down a first failed release or
reactivates the previously verified digest/config pair. A manual rollback also
revalidates the recorded archives, ownership/modes, host boundary, and both
image digests before activation. See `docs/runbooks/openclaw.md` for the exact
commands and the separate offline recovery export/manifest contract.

## Production checks

After promotion, verify:

1. the active release audit succeeds without a second authenticated smoke;
2. Compose reports the Gateway healthy at `/readyz`;
3. the active release record contains the expected Gateway digest, CTF digest,
   config commit, and both bundle hashes;
4. a configured CTF channel creates a sandbox from the approved CTF digest;
5. the skill-sync timer remains active and its credential is absent from the
   Gateway environment and mounts.
