# OpenClaw immutable runtime

OpenClaw keeps its dedicated unprivileged LXC (`openclaw`, VMID 118,
`192.168.0.5`). The host is a Docker/Compose runtime. Image assembly, plugin
installation, and patching happen only in CI.

## One desired-state descriptor

Every production release contains exactly four authoritative identities:

| Item | Identity |
| --- | --- |
| Public source | exact homelab Git commit |
| Private configuration | exact `openclaw-setup` Git commit |
| Gateway | `ghcr.io/holybaechu/homelab-openclaw-gateway@sha256:<64 hex>` |
| CTF image | `ghcr.io/holybaechu/homelab-openclaw-ctf@sha256:<64 hex>` |

The uploaded tar SHA-256 proves transport integrity only. Runtime and private
configuration archive hashes are not desired-state coordinates.

`scripts/ci/compose_release_engine.py` builds the descriptor and carries its
own exact engine version inside the bundle. The small stable launcher verifies
the upload and embedded engine, then that engine:

1. acquires the target `flock`;
2. validates regular paths, exact metadata, source identities, and image
   repositories/digests;
3. stores immutable source in a SHA-addressed release directory;
4. validates the current `/etc/openclaw/secrets/openclaw.json` component
   bundle and renders three mode-0600 files into an inactive runtime slot;
5. runs `docker compose config`, pulls the two exact images, and runs
   `up -d --wait --no-build --remove-orphans`;
6. verifies the running Gateway digest and health; and
7. runs package-owned readiness, rejects unauthenticated and wrong-token
   control requests, then proves a bearer-authenticated request before
   atomically committing `pending/current/previous` state.

No secret value or secret hash is written to the descriptor, release state,
or command output. A failed candidate recreates the previous source with the
current component bundle. The next operation resolves an interrupted pending
transaction before accepting new work.

Operator commands:

```sh
/usr/local/libexec/homelab-release audit --target openclaw
/usr/local/libexec/homelab-release rollback --target openclaw
```

`audit` recreates and proves the current release with the current component
bundle. `sync-secrets` atomically installs one uploaded bundle and performs the
same recreation; choose that operation in the manual OpenClaw workflow for a
rotation with no image build, release archive, or image pull.

## Host and isolation boundary

The Gateway runs as UID/GID 1000 with a read-only root filesystem, all Linux
capabilities dropped, and only the numeric host Docker group added. It uses
the host Docker socket to create session CTF containers. The dedicated CTF
bridge denies private, loopback, link-local, and tailnet destinations.
nftables accepts Gateway ingress only from the apps host proxy.

Mutable Gateway state remains in `/var/lib/openclaw`; model authorization
remains in `/home/openclaw/.config/openclaw`. The exact private configuration
is mounted read-only from the active runtime slot. Only the three service
credential files are exposed to the Gateway.

## Skill promotion

The production LXC has no GitHub client credential, collection user, timer,
or promotion state. The private `holybaechu/openclaw-setup` repository owns a
scheduled workflow that:

1. joins the management tailnet and uses pinned SSH trust;
2. streams a bounded exporter to the host without installing it;
3. validates UTF-8 regular skill files, ownership, paths, sizes, and
   credential patterns both remotely and in CI;
4. creates a content-derived pull request and waits for its independent tests;
5. squash-merges the validated change; and
6. dispatches the exact merged private-config commit to the complete OpenClaw
   deployment lane.

The GitHub credential exists only in the private repository Actions
environment and is never sent to production.
