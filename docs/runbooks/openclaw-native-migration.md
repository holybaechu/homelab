# OpenClaw Native LXC Migration

The completed migration used two public IaC deployments and one audited
transfer. This document records that transaction and the current rollback
contract. Do not add model credentials, agents, channels, skills, or browser
state as part of foundation maintenance.

## Completed phase 1: inactive staging

Hard precondition: the router must reserve `192.168.0.5` to
`02:00:00:BA:EC:05`, or exclude that address from dynamic allocation. This
production identity is hardcoded in
`infra/opentofu/envs/prod/containers.auto.tfvars` and in the fail-closed LXC
preflight. An ARP non-response proves only momentary vacancy; verify the
router's authoritative reservation table whenever this tracked identity is
changed. No separate GitHub environment variable duplicates the IaC value.

The first CD run used a temporary tracked transition lane. It created
unprivileged VMID 118 (`openclaw`, `192.168.0.5`) after proving its
VMID, IP, MAC, and datastore allocation are safe. The stage-only OpenTofu guard
rejects every action except an exact create of VMID 118 (or no-op resources),
so drift in an existing LXC cannot be applied accidentally.

The narrow bootstrap touches only VMID 118. It does not run homelab-storage,
legacy-LXC, low-ID cutover, Docker-host, tailnet, or all-Debian bootstrap work.
The narrow stage then installs the integrity-pinned Node.js and OpenClaw
releases, proxy-only nftables policy, and system unit. The unit remains
disabled and stopped. A dedicated route role copies only the platform Compose
definition, Traefik static configuration, and complete dynamic route file. If
the directory mount or static configuration changed, it runs exactly a
`--no-deps` Traefik recreation; it never invokes the broad Compose or Arcane
roles. A route-only update uses Traefik's file watch without a container
restart.

Before OpenTofu, CD snapshots every long-lived platform, media, code,
OpenClaw, and Arcane container. Phase 1 permits only Traefik's identity to
change. The post-stage proof is mandatory and fails if AdGuard, DDNS, media,
code, the retained Docker Gateway, Arcane, or its socket proxy restarted or
was recreated. The tracked HTTPS route is expected to return a
certificate-valid `502` while the native backend is deliberately unavailable.
The existing Docker Gateway on VMID 110 remains running and Arcane continues
to manage it.

That stage established that VMID 118 requires no Docker engine, socket,
nesting, TUN device, or Proxmox bind mount.

## Completed audited transfer

The one-time manual migration workflow alone stopped the Docker Gateway. It transferred
the clean private checkout, runtime state, and auth-profile directory directly
between the two hosts through the pinned CI SSH trust chain. Archives are not
persisted on the runner. The converter changes only `config/openclaw.json` and
`README.md`, validates the active pinned schema, runs `secrets audit --check`,
inspects the staged diff for the real token, and then creates the private
repository's native-LXC commit.

The transfer uses paired, persistent systemd guards with root-only markers and
timing metadata on both hosts. They survive runner loss and host reboot. The
source timestamp is validated only as migration transaction metadata; it never
authorizes a restore. Once its active deadline expires (or the LXC reboots),
the destination runtime-masks native
OpenClaw, removes its exact persistent enable symlink, and proves the service
disabled, inactive, and without a listener. The source guard never infers that
cross-host proof from a local deadline or health request: without an exact
orchestrator force marker it continuously fences the retained Docker Gateway.
After runner loss, rerun the manual workflow in recovery mode; it first proves
the persistent native fence, then authorizes and verifies Docker restoration.
Do not bypass a failed rollback verification.

Before activating native OpenClaw, the workflow re-proves both guards and their
deadlines. Before releasing them, it proves the backend directly, an explicit
shared-token-authenticated Gateway handshake with the intentional authorization
boundary of no operator scope, and the real HTTPS Traefik path without disabling
TLS verification. First device pairing remains a user action. It then writes
paired root-owned validation markers
atomically, retires only the OpenClaw Arcane sync, and verifies that the old
Gateway remains stopped and retained.

The manual workflow did not rerun OpenTofu or broad bootstrap. It had an early
recovery-only gate before the same narrow, idempotent stage and kept the
actual transfer as its final mutating deployment step. A fresh identity
baseline protects Traefik, every platform sibling, media, code, and the Arcane
socket proxy throughout migration. Only the old Gateway's lifecycle and the
Arcane service are excluded; the retained Gateway's immutable container and
image identity are checked separately. Arcane retirement occurs only inside
the dedicated finalizer after paired validation markers exist. This lets an
exact partial marker or abandoned transfer fail back safely without allowing
a normal role run to start both Gateways or restart unrelated workloads.
In that finalizer-only mode, both Arcane's temporary-key start and its final
keyless start run exactly `docker compose up -d --force-recreate --no-deps
arcane`; the socket proxy is neither recreated nor orphan-pruned. Ordinary
Arcane reconciliation keeps its existing whole-project behavior.

## Current steady state: tracked native cutover

The public repository now records the validated native steady state as one
atomic ownership contract:

1. `openclaw_native_activate: true`,
   `openclaw_docker_rollback_activate: false`, and the static tracked
   `apps/compose/platform/dynamic/routes.yml` backend
   `http://192.168.0.5:18789` are committed together. Never merge or deploy a
   partial tuple;
2. the old Docker Gateway remains stopped without removing its checkout, state,
   token, image, Compose files, or container;
3. Traefik retains the backend `http://192.168.0.5:18789`;
4. normal whole-project reconciliation semantics apply to future shared
   platform Compose-file changes; the phase-1 isolated Traefik recreation
   override is gone;
5. the one-time migration input, recovery and terminal-transfer steps,
   transition gate, narrow bootstrap/stage workflow lane, and temporary
   identity-proof surface are absent from CD. The
   active native role removes only its destination transition marker; retain
   the Docker-host `native-cutover-validated` marker as the durable rollback
   hold that keeps the old Gateway stopped and its Arcane sync retired; and
6. `apps/compose/openclaw/` is absent from the Arcane fast-path selector, and
   the exact `apps/compose/platform/dynamic/routes.yml` ownership tuple is a
   full-deployment path. Rollback-manifest, README, and route-owner changes
   therefore run the marker-aware pre-site fence rather than request a missing
   Arcane project or bypass the exclusive-owner check; and
7. every normal full deployment runs native, proxy, firewall,
   private-repository, and rollback-retention validation; and
8. the first native-primary full deployment creates
   `/opt/homelab-control/openclaw/retained-gateway-identity.json` on the
   Docker host. It is a canonical, root-owned mode `0600` checkpoint containing
   only the schema, full container ID, container creation time, local image ID,
   and immutable image reference. Later deployments compare it byte-for-byte;
   they never overwrite, remove, or regenerate it. The one-time bootstrap is
   authorized only while both this checkpoint and the persistent verifier at
   `/usr/local/libexec/openclaw-retained-gateway` are absent. If either object
   already exists, the fence uses require-only comparison, so deletion of the
   checkpoint after bootstrap fails closed.

Before either steady-state owner can be reconciled, CD checks the exact source
marker, mutually exclusive ownership flags, complete router contract, and
backend. It then transfers a bundled verifier to a random file under `/run`,
strictly checks the retained Compose asset, deployment environment, private
Git repository, current Gateway token, config, image, container hardening,
mounts, loopback publication, and rollback network, and removes the temporary
verifier in an `always` cleanup. The persistent foundation copy and final
validation repeat the retained proof after site reconciliation.

Native-primary checkpoint seeding is allowed exactly once, by the pre-site
fence, while both bootstrap artifacts are absent and only from the exact
stopped and proxy-disconnected retained container. Foundation and validation
are require-only. Its config may be either the original foundation form or the
exact hardened rollback form. The mutable private
config, runtime state, auth-profile state, and token are deliberately not
embedded in the identity checkpoint; their live boundaries are revalidated on
every full deployment. Replacing the retained container or image is therefore
not an ordinary repair: it requires a separately reviewed migration contract,
not deletion or regeneration of this checkpoint.

This retained-host proof assumes the Docker host's root account, the homelab
service UID/GID, Docker daemon, and the locally administered Arcane control
plane remain trusted. UID 1000 owns the retained Compose and mutable runtime
trees by design, and Arcane retains its broader compose-root management mount;
the verifier detects drift at each fence but is not a sandbox against a
simultaneously compromised local service principal. Root-owned setup/config,
secret, control-marker, checkpoint, and verifier boundaries protect the
authorization material. Treat compromise of any named trusted principal as a
host incident and do not use the ordinary rollback procedure until the host
and retained assets have been rebuilt under a separately reviewed recovery.

The native Gateway binds its LXC address because Traefik is on another host.
Guest nftables accepts port 18789 only from `192.168.0.3`; the Gateway retains
token authentication, exact HTTPS origin validation, rate limiting, and a
disabled terminal. The Control UI is never published directly to the LAN.

## Rollback

During the one-time transfer, workflow failures triggered guarded rollback in
the cleanup handler and a rerun performed the same recovery transaction. Those
transition-only workflow paths are now retired; the source guard still never
starts Docker from local timeout or native HTTP failure alone.

Afterward, rollback is a tracked, mutually exclusive full deployment. Do not
delete `/opt/homelab-control/openclaw/native-cutover-validated` from the Docker
host: it is the permanent source hold and rollback authorization.
Also do not edit or delete
`/opt/homelab-control/openclaw/retained-gateway-identity.json`; a missing or
mismatched checkpoint intentionally blocks rollback while native is still
serving.
Do not re-register OpenClaw with Arcane.

First stop unrelated changes and decide explicitly whether the native or
retained Docker state is authoritative. Reconcile the chosen config, runtime
state, and auth-profile state while both Gateways are not simultaneously
active. Before changing the public repository, verify that normal steady-state
validation passes and that the retained source config has the exact HTTPS
origin, file-backed token, rate limit, disabled terminal, disabled Tailscale
auth, empty trusted-proxy list, and no real-IP fallback. The rollback role
fails closed if any retained asset, container identity, image, current token,
source marker, hardened config contract, rollback network, or proxy-alias
ownership differs.

The pre-site fence performs this proof before the native role can stop native
OpenClaw. A rollback rerun may find the checkpoint-matching retained container
already running and healthy but not yet attached with its proxy alias; that
specific interrupted state is resumable only when the alias is unowned. Final
validation still requires the unique `openclaw-rollback` alias. In the reverse
rollback-to-native direction, when the retained container is running, the fence
first requires its hardened config and existing checkpoint, then stops and
disconnects it and repeats the stopped proof before native can be reconciled.
Thus identity or asset drift never causes the currently serving owner to be
stopped first.

On VMID 110, with the retained Gateway still stopped, prepare and commit the
Docker-specific private config before the public ownership commit. This edit
is idempotent, preserves the protected file metadata, and refuses an
unexpected base token or listener contract:

```sh
set -eu
setup=/opt/homelab-compose/openclaw-setup
config="$setup/config/openclaw.json"
compose=/opt/homelab-compose/openclaw
checkpoint=/opt/homelab-control/openclaw/retained-gateway-identity.json
test "$(cat /opt/homelab-control/openclaw/native-cutover-validated)" = \
  homelab-openclaw-native-migration-v1
test -f "$checkpoint"
test ! -L "$checkpoint"
test "$(stat -c '%u:%g %a %h' "$checkpoint")" = '0:0 600 1'
cd "$compose"
test -n "$(docker compose ps --all -q openclaw-gateway)"
test -z "$(docker compose ps --status running -q openclaw-gateway)"
cd "$setup"
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain=v1)"
python3 - "$config" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

path = Path(sys.argv[1])
metadata = path.stat(follow_symlinks=False)
if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
    raise SystemExit("refusing a non-regular OpenClaw config")
document = json.loads(path.read_text(encoding="utf-8"))
gateway = document.get("gateway")
if not isinstance(gateway, dict):
    raise SystemExit("gateway must be an object")
auth = gateway.get("auth")
provider = (document.get("secrets") or {}).get("providers", {}).get(
    "gateway_token_file"
)
expected_token = {
    "source": "file",
    "provider": "gateway_token_file",
    "id": "value",
}
expected_provider = {
    "source": "file",
    "path": "/run/secrets/openclaw_gateway_token",
    "mode": "singleValue",
}
if (
    gateway.get("mode") != "local"
    or gateway.get("port") != 18789
    or gateway.get("bind") != "lan"
    or not isinstance(auth, dict)
    or auth.get("mode") != "token"
    or auth.get("token") != expected_token
    or provider != expected_provider
):
    raise SystemExit("refusing unexpected retained Docker config")
auth["allowTailscale"] = False
auth["rateLimit"] = {
    "maxAttempts": 10,
    "windowMs": 60000,
    "lockoutMs": 300000,
    "exemptLoopback": True,
}
gateway["controlUi"] = {
    "enabled": True,
    "allowedOrigins": ["https://openclaw.home.hchu.me"],
}
gateway["terminal"] = {"enabled": False}
gateway["trustedProxies"] = []
gateway["allowRealIpFallback"] = False
gateway["tailscale"] = {"mode": "off", "resetOnExit": False}
descriptor, temporary = tempfile.mkstemp(
    prefix=".openclaw.json.rollback.", dir=path.parent
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
        os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
        json.dump(document, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
cd "$compose"
docker compose run -T --rm --no-deps --entrypoint node \
  openclaw-gateway dist/index.js config validate --json |
  python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if value.get("valid") and not value.get("warnings") else 1)'
docker compose run -T --rm --no-deps --entrypoint node \
  openclaw-gateway dist/index.js secrets audit --check --json |
  python3 -c 'import json,sys; value=json.load(sys.stdin); summary=value.get("summary") or {}; raise SystemExit(0 if value.get("status") == "clean" and summary.get("plaintextCount") == 0 and summary.get("unresolvedRefCount") == 0 else 1)'
cd "$setup"
git diff --check -- config/openclaw.json
git diff --no-ext-diff --no-textconv -- config/openclaw.json
git add -- config/openclaw.json
git diff --cached --check
git -c user.name='Homelab Production Deployer' \
  -c user.email='homelab-production-deployer@users.noreply.github.com' \
  -c core.hooksPath=/dev/null \
  commit --no-gpg-sign -m 'Prepare retained Docker OpenClaw rollback'
test -z "$(git status --porcelain=v1)"
```

The private checkout intentionally has no deployment remote. Preserve that
local commit with the retained assets; do not add a remote or copy the private
config into the public repository.

Create one public rollback commit with exactly these three changes. This
standard-library script deliberately refuses unexpected source values:

```sh
set -eu
python3 - <<'PY'
from pathlib import Path

changes = {
    Path("infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"): (
        "openclaw_native_activate: true\n",
        "openclaw_native_activate: false\n",
    ),
    Path("infra/ansible/inventory/prod/group_vars/all.yml"): (
        "openclaw_docker_rollback_activate: false\n",
        "openclaw_docker_rollback_activate: true\n",
    ),
    Path("apps/compose/platform/dynamic/routes.yml"): (
        "          - url: http://192.168.0.5:18789\n",
        "          - url: http://openclaw-rollback:18789\n",
    ),
}
for path, (old, new) in changes.items():
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"refusing unexpected {path}: expected one source value")
    path.write_text(text.replace(old, new), encoding="utf-8")
PY
git diff --check
python3 -m pytest -q \
  tests/openclaw/test_docker_rollback.py \
  tests/openclaw/test_native_migration.py \
  tests/repo/test_deployment_scope.py
git diff -- \
  infra/ansible/inventory/prod/group_vars/svc_openclaw.yml \
  infra/ansible/inventory/prod/group_vars/all.yml \
  apps/compose/platform/dynamic/routes.yml
git add \
  infra/ansible/inventory/prod/group_vars/svc_openclaw.yml \
  infra/ansible/inventory/prod/group_vars/all.yml \
  apps/compose/platform/dynamic/routes.yml
git commit -m 'Activate retained Docker OpenClaw rollback'
git push
```

The normal full CD run validates the tuple and retained checkpoint while native
still serves, then stops and disables native OpenClaw and proves its listener
absent. It starts only the checkpointed container without pull or recreation,
attaches it to `homelab_proxy` with the unique `openclaw-rollback` alias,
hot-reloads the static route, and proves the certificate-valid HTTPS Control
UI. It also proves that the source hold remains root-owned mode `0600` and that
Arcane's OpenClaw sync remains retired. If CD fails, do not manually start
either Gateway; inspect the failed proof and rerun the same commit after
correcting the tracked or retained-state precondition.

Run the complete proof again from an authorized checkout if needed:

```sh
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml \
  infra/ansible/playbooks/validate.yml
curl --fail --show-error --silent \
  https://openclaw.home.hchu.me/readyz >/dev/null
```

To restore native service, first reconcile any state that advanced on Docker
into the native destination while maintaining single-writer ownership. Then
make the exact reverse three-file commit. The pre-site fence requires the
existing checkpoint and exact running retained assets before it stops and
disconnects Docker, then revalidates the stopped container before the native
role can start:

```sh
set -eu
python3 - <<'PY'
from pathlib import Path

changes = {
    Path("infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"): (
        "openclaw_native_activate: false\n",
        "openclaw_native_activate: true\n",
    ),
    Path("infra/ansible/inventory/prod/group_vars/all.yml"): (
        "openclaw_docker_rollback_activate: true\n",
        "openclaw_docker_rollback_activate: false\n",
    ),
    Path("apps/compose/platform/dynamic/routes.yml"): (
        "          - url: http://openclaw-rollback:18789\n",
        "          - url: http://192.168.0.5:18789\n",
    ),
}
for path, (old, new) in changes.items():
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"refusing unexpected {path}: expected one source value")
    path.write_text(text.replace(old, new), encoding="utf-8")
PY
git diff --check
python3 -m pytest -q \
  tests/openclaw/test_docker_rollback.py \
  tests/openclaw/test_native_migration.py \
  tests/repo/test_deployment_scope.py
git add \
  infra/ansible/inventory/prod/group_vars/svc_openclaw.yml \
  infra/ansible/inventory/prod/group_vars/all.yml \
  apps/compose/platform/dynamic/routes.yml
git commit -m 'Restore native OpenClaw ownership'
git push
```

Never run both Gateways against channel or model credentials at the same time,
never use an untracked route override, and never remove the permanent source
hold during either direction.
