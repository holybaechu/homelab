# OpenClaw Native LXC Migration

The migration is deliberately split into two public IaC deployments and one
audited transfer. Do not add model credentials, agents, channels, skills, or
browser state during this process.

## Phase 1: inactive staging

Hard precondition: the router must reserve `192.168.0.5` to
`02:00:00:BA:EC:05`, or exclude that address from dynamic allocation. This
production identity is hardcoded in
`infra/opentofu/envs/prod/containers.auto.tfvars` and in the fail-closed LXC
preflight. An ARP non-response proves only momentary vacancy; verify the
router's authoritative reservation table whenever this tracked identity is
changed. No separate GitHub environment variable duplicates the IaC value.

The first CD run uses the temporary tracked `OPENCLAW_NATIVE_TRANSITION` lane.
It creates unprivileged VMID 118 (`openclaw`, `192.168.0.5`) after proving its
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

Phase 1 is safe to repeat. Validation requires no Docker engine, socket,
nesting, TUN device, or Proxmox bind mount in VMID 118.

## Audited transfer

Only the manual migration workflow may stop the Docker Gateway. It transfers
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

The manual workflow does not rerun OpenTofu or broad bootstrap. It has an early
recovery-only gate before the same narrow, idempotent stage and keeps the
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

## Phase 2: tracked cutover

After the native Gateway and the real HTTPS path through Traefik host
`192.168.0.3` are healthy, commit the steady state in the public repository:

1. atomically commit the three ownership fields: set
   `openclaw_native_activate: true`, keep
   `openclaw_docker_rollback_activate: false`, and keep the static tracked
   `apps/compose/platform/dynamic/routes.yml` backend exactly
   `http://192.168.0.5:18789`. Never merge or deploy a partial tuple;
2. keep the old Docker Gateway stopped without removing its checkout, state,
   token, image, Compose files, or container;
3. retain the already-deployed Traefik route to `http://192.168.0.5:18789`;
4. remove the phase-1 `runtime_file_force_recreate_services` override after
   the Traefik directory mount has landed, restoring normal whole-project
   reconciliation semantics for future shared Compose-file changes;
5. remove the one-time `migrate_openclaw_native` workflow input, recovery step,
   terminal transfer step, `OPENCLAW_NATIVE_TRANSITION` gate, and narrow
   bootstrap/stage workflow lane after the tracked steady state validates. The
   active native role removes only its destination transition marker; retain
   the Docker-host `native-cutover-validated` marker as the durable rollback
   hold that keeps the old Gateway stopped and its Arcane sync retired; and
6. remove the `apps/compose/openclaw/` Arcane fast-path prefix from
   `select-deployment-scope.py`. After the finalizer retires that sync, later
   rollback-manifest or README changes must take the full, marker-aware path
   rather than request a missing Arcane project; and
7. run native, proxy, firewall, private-repository, and rollback-retention
   validation.

The native Gateway binds its LXC address because Traefik is on another host.
Guest nftables accepts port 18789 only from `192.168.0.3`; the Gateway retains
token authentication, exact HTTPS origin validation, rate limiting, and a
disabled terminal. The Control UI is never published directly to the LAN.

## Rollback

Before the final public cutover, workflow failures trigger guarded rollback in
the cleanup handler. If the runner is lost, rerunning the manual workflow
performs the same recovery transaction; the source guard never starts Docker
from local timeout or native HTTP failure alone.

Afterward, rollback is a tracked, mutually exclusive full deployment. Do not
delete `/opt/homelab-control/openclaw/native-cutover-validated` from the Docker
host: it is the permanent source hold and rollback authorization.
Do not re-register OpenClaw with Arcane.

First stop unrelated changes and decide explicitly whether the native or
retained Docker state is authoritative. Reconcile the chosen config, runtime
state, and auth-profile state while both Gateways are not simultaneously
active. Before changing the public repository, verify that normal steady-state
validation passes and that the retained source config has the exact HTTPS
origin, file-backed token, rate limit, disabled terminal, disabled Tailscale
auth, empty trusted-proxy list, and no real-IP fallback. The rollback role
fails closed if any retained asset, container identity, image, source marker,
or config contract differs.

On VMID 110, with the retained Gateway still stopped, prepare and commit the
Docker-specific private config before the public ownership commit. This edit
is idempotent, preserves the protected file metadata, and refuses an
unexpected base token or listener contract:

```sh
set -eu
setup=/opt/homelab-compose/openclaw-setup
config="$setup/config/openclaw.json"
compose=/opt/homelab-compose/openclaw
test "$(cat /opt/homelab-control/openclaw/native-cutover-validated)" = \
  homelab-openclaw-native-migration-v1
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

The normal full CD run validates the tuple before mutation, stops and disables
native OpenClaw and proves its listener absent, starts only the exact retained
container without pull or recreation, attaches it to `homelab_proxy` with the
unique `openclaw-rollback` alias, hot-reloads the static route, and proves the
certificate-valid HTTPS Control UI. It also proves that the source hold remains
root-owned mode `0600` and that Arcane's OpenClaw sync remains retired. If CD
fails, do not manually start either Gateway; inspect the failed proof and rerun
the same commit after correcting the tracked or retained-state precondition.

Run the complete proof again from an authorized checkout if needed:

```sh
ansible-playbook -i infra/ansible/inventory/prod/hosts.yml \
  infra/ansible/playbooks/validate.yml
curl --fail --show-error --silent \
  https://openclaw.home.hchu.me/readyz >/dev/null
```

To restore native service, first reconcile any state that advanced on Docker
into the native destination while maintaining single-writer ownership. Then
make the exact reverse three-file commit; the pre-site fence stops Docker
before the native role can start:

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
