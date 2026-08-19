# OpenClaw immutable runtime

OpenClaw keeps its dedicated unprivileged LXC (`openclaw`, VMID 118,
`192.168.0.5`). The LXC is now only a Docker/Compose runtime. It never builds
an image, downloads Node/npm, installs a plugin, or applies a JavaScript patch
during production deployment.

## One promoted release

A production promotion identifies exactly one state:

| Item | Required identity |
| --- | --- |
| Gateway | registry-qualified `repository@sha256:<64 hex>` |
| CTF tool image | registry-qualified `repository@sha256:<64 hex>` |
| Public runtime bundle | deterministic uncompressed tar SHA-256 |
| Private config bundle | exact 40-character Git commit and tar SHA-256 |
| Deployment | exact homelab 40-character commit |

`scripts/ci/openclaw_release.py` creates and validates the canonical release
manifest. CI builds Gateway plugins/patches and the CTF toolchain from exact
base-image digests under `infra/openclaw/`; production receives only the two
resulting immutable refs and the two verified bundles.

`scripts/ci/deploy_openclaw_release.py` is the only activator. On the LXC it:

1. locks `/opt/openclaw/state` with a nonblocking kernel lock;
2. rejects symlinks, traversal, incorrect modes, and bundle hash drift;
3. pulls and inspects both exact OCI digests;
4. runs Compose with `--no-build`;
5. waits on `/readyz` and makes exactly one authenticated smoke request;
6. atomically replaces one root-owned `release-state.json` containing the
   current and previous immutable manifests.

If activation fails, the same process restores the previous Gateway digest,
CTF digest, config commit, and runtime bundle on the same LXC and proves it
healthy. A failed first activation is brought down rather than left partially
running. `audit` validates the active digest/config without performing another
authenticated smoke. Manual rollback is:

```sh
sudo /usr/local/libexec/deploy_openclaw_release.py \
  --install-root /opt/openclaw \
  --secret-root /etc/openclaw/secrets \
  rollback
```

## Offline recovery artifact

Before the first immutable activation, export the retired Docker Gateway's
known digest, protected config bundle, and OCI archive. The known legacy image
is fixed in the contract; tags are rejected.

```sh
python scripts/ci/openclaw_release.py export-legacy-recovery \
  --config-commit "$EXACT_CONFIG_COMMIT" \
  --config-archive recovery/legacy-config.tar \
  --gateway-oci-archive recovery/legacy-gateway.oci \
  --output recovery/legacy-recovery.json

python scripts/ci/openclaw_release.py verify-legacy-recovery \
  --manifest recovery/legacy-recovery.json \
  --config-archive recovery/legacy-config.tar \
  --gateway-oci-archive recovery/legacy-gateway.oci
```

Store all three files in the protected offline recovery location before
retiring the old deployment. This export is not uploaded to the LXC and is not
an input to routine activation or validation. Repository tests use a fake OCI
archive to verify the export contract; they do not claim that a production
export has occurred.

For the active immutable release, export both OCI archives and run the
`recovery` command to create a hash-complete offline manifest. Keep its release
manifest, runtime/config tars, and OCI archives together. Recovery order is
image load, exact RepoDigest inspection, then activation through the same
deployer—never a host build.

## Host and secret boundary

The Gateway runs as UID/GID 1000. Root owns `/etc/openclaw/secrets`; only the
three Gateway secret files are bind-mounted read-only with root ownership and
group-readable mode `0440`. Compose adds the numeric host Docker group so the
Gateway can use `/var/run/docker.sock`; deployment validates the socket type,
GID, and group access before mutation. The dedicated CTF bridge denies private,
loopback, link-local, and tailnet destinations. nftables accepts port 18789 only
from the application host's Traefik proxy.

Mutable state remains in `/var/lib/openclaw`; model auth remains in
`/home/openclaw/.config/openclaw`. The promoted config must use container paths
under `/home/node` and `/etc/openclaw` and file-backed SecretRefs.

## Autonomous skill promotion

Skill sync remains deliberately outside the Gateway. Every five minutes,
`openclaw-skill-sync.timer` runs the compact host service as the separate
`openclaw-skill-sync` identity. It has read-only ACLs for the main and CTF
`skills/` roots and write access only to its own state. systemd loads
`OPENCLAW_SKILL_SYNC_GITHUB_TOKEN` as a credential; Compose neither mounts nor
exposes it. The service preserves the existing validate, branch, PR, checks,
squash-merge, and exact promotion-dispatch behavior.

## Operational verification

Scheduled validation runs the deployer's nonmutating `audit`, verifies the
active manifest equals its materialized release, checks both RepoDigests,
confirms root/config modes, Docker socket group access, CTF firewall rules,
Compose hardening, `/readyz`, and the skill-sync timer. Production deployment
itself owns the single authenticated smoke and rollback decision.
