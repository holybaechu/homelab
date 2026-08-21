# Homelab Compose deployment and recovery

## Normal architecture

Production retains three LXCs: `docker_apps`, `tailnet`, and `openclaw`. The
application LXC runs one Compose project, `homelab`, from the self-contained
`apps/compose/homelab` package. The package contains its Compose model,
nonsecret configuration, strict secret-bundle materializer, release metadata,
and semantic smoke test.

CI bundles that directory from one exact commit. The stable host launcher
checks the upload checksum and embedded engine digest. The versioned engine
stores immutable source under `/opt/homelab/compose-releases`, renders the current
`/etc/homelab/secrets/apps.json` into an inactive runtime slot, validates
Compose, pulls, activates with `--no-build`, waits for services, and runs the
package `smoke.sh`. Ansible creates host primitives only; application releases
do not traverse infrastructure reconciliation.

Compose owns the `homelab_proxy` network and creates the two stable explicitly
named data volumes on first activation. Durable application data remains in
those volumes and `/srv/homelab` mounts. The engine never stops Compose with
`--volumes` and therefore intentionally retains data.

## Rollback behavior

The release engine records `pending`, `current`, and `previous` under
`/opt/homelab/compose-control` and holds one host `flock` for deploy, audit, secret sync,
and rollback. A candidate becomes current only after Compose and the package
smoke contract pass. If activation fails, the engine rebuilds the previous
source into the other runtime slot with the current component secrets. The
next operation resolves any interrupted transaction before doing new work.

The engine derives its expected service set from Compose, and the semantic
smoke derives ingress endpoints from Compose labels. Adding or removing a
service does not require another service or route list in Ansible or CI. A
service whose scratch image cannot provide a healthcheck declares
`homelab.health=process`; the engine derives that exception from the rendered
model and requires one running, non-restarting container with zero activation
restarts before smoke and state commit.

## Bounded storage and image retention

Before any image pull, the engine refuses to proceed unless the target release
filesystem has at least 4 GiB free for apps or 12 GiB free for OpenClaw. It
retains immutable source only for release records still reachable as
`current`, `previous`, or an interrupted `pending` transaction.

The engine retries deferred target-owned source and image cleanup when an
operation starts, then performs retention again after activation and durable
state commit. It considers only image references previously observed in the
managed Compose project or an unreferenced managed release, and it protects
every current, previous, or pending release reference. It never prunes volumes.
Docker also refuses to remove an image used by another live container, so an
active OpenClaw session container keeps its image after the parent release no
longer references it. Cleanup failure does not turn a successfully committed
activation into a reported deployment failure; the deferred-ref journal makes
a later operation retry it.

## Reconstruction

Run the explicit `apps-host` infrastructure reconcile. Confirm Docker,
`/srv/homelab`, the persistent directories, the mounted root CA, the stable
launcher, and the private `apps.json` component bundle exist. Then manually run
the ordinary complete apps workflow. Initial activation has no previous
release; a failure stops the candidate and leaves no current release. There is
no alternate stack or second deployment state machine.

For a credential-only rotation, select `sync-secrets` in the target's manual
workflow. That path uploads one component JSON document, atomically installs
it, and recreates the current release; it does not build images, construct or
upload a release archive, or pull images.

## Host-first activation and launcher changes

The stable launcher is infrastructure-owned and is never uploaded by an
application workflow. A merge to `main` can automatically start a runtime
workflow, so installing the launcher after merge is too late. For the first
activation and every future `release_launcher.py` change, use this order:

1. Keep the change unmerged and check out its exact candidate commit on a
   trusted, tailnet-connected Ansible controller with pinned SSH trust.
2. From that candidate checkout, reconcile `apps-host` and `openclaw-host`
   explicitly. These out-of-band runs install the candidate launcher before any
   automatic runtime trigger:

   ```sh
   export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
   ansible-playbook -i infra/ansible/inventory/prod/topology.json \
     infra/ansible/playbooks/reconcile.yml -e homelab_unit=apps-host
   ansible-playbook -i infra/ansible/inventory/prod/topology.json \
     infra/ansible/playbooks/reconcile.yml -e homelab_unit=openclaw-host
   ```

3. Compare each host's `/usr/local/libexec/homelab-release` SHA-256 with the
   candidate `scripts/ci/release_launcher.py`; do not merge if either differs.
4. Take the documented PVE snapshot and data backup, then merge while holding
   the apps production-environment approval. The OpenClaw workflow may remain
   queued behind the shared control-plane lock.
5. The previous apps host has an externally created `homelab_proxy` network
   without Compose ownership labels. Run this bounded transition on the apps
   host in the maintenance window:

   ```sh
   previous_stack="$(readlink -e /opt/homelab/current/homelab)"
   test -d "$previous_stack"
   test -f "$previous_stack/.env"
   test -f "$previous_stack/.homelab/artifacts.env"
   docker compose --project-name homelab --project-directory "$previous_stack" \
     --env-file "$previous_stack/.env" \
     --env-file "$previous_stack/.homelab/artifacts.env" \
     -f "$previous_stack/compose.yml" down --remove-orphans
   test "$(docker network inspect homelab_proxy --format '{{len .Containers}}')" = 0
   docker network rm homelab_proxy
   ```

   Skip these commands when inspection already shows both
   `com.docker.compose.project=homelab` and
   `com.docker.compose.network=proxy`.
6. Approve the apps job immediately afterward. The new release creates the
   same named network with Compose labels. Until the transition is complete,
   the new engine detects the unowned network before image pull, `up`, or
   `down`, restores its empty pending state, and leaves the previous project
   running. The normal apps and OpenClaw workflows may then activate the new
   engine. OpenClaw keeps the
   whole workflow in the non-cancelling `prod-control-plane` queue, so its two
   image builds run in parallel within one admitted workflow. GitHub does not
   guarantee dispatch-order admission to a concurrency group, so each automatic
   runtime job compares its exact lane input paths with current homelab `main`
   immediately before mutation. Unrelated newer documentation or test commits
   do not suppress a deployment, while a newer package, topology, transport, or
   workflow change makes the older run fail without touching the host. An
   automatic OpenClaw run also checks out private-config `main` again directly
   before the deploy command and rejects a promotion whose bound commit no
   longer matches that tip. Manual dispatch remains the explicit rollback path.
7. After both first deployments and launcher audits succeed, archive the old
   control directories and retire old unit/account/executable/individual-secret
   artifacts using the immutable pre-simplification reference in
   `docs/runbooks/recovery.md`. Record that one-time host operation separately;
   current reconciliation owns only host primitives and carries no recurring
   conversion or obsolete-host cleanup branch.

On a new/rebuilt host, complete the same out-of-band host reconcile before
allowing its first runtime job. Prepare all component documents before the
merge; the host-primitives run does not install application secrets.

The control plane uses `compose-releases`, `compose-runtime`, and
`compose-control` below each target install root, so it never interprets an
unrelated state schema. Its first activation has no recorded previous release.
Take a PVE snapshot and a separate durable-data/secret backup, use a maintenance
window, then run one complete target deployment. After its audit passes,
archive or remove unreferenced older control directories; do not delete
`/srv/homelab`, `/var/lib/openclaw`, or named Compose volumes.

Create all four versioned component documents before releasing production
jobs. Exact runtime shapes are documented in `secrets/README.md` and the apps
package README. PVE apply and tailnet use, respectively:

```json
{"component":"pve","version":1,"values":{"deploy_ssh_public_keys":["ssh-ed25519 ..."]}}
{"component":"tailnet","version":1,"values":{"tailscale_auth_key":"tskey-auth-..."}}
```

Existing application hashes can be copied from the live AdGuard and
qBittorrent configuration. To deliberately replace them, generate compatible
values without putting plaintext in Git:

```sh
read -rsp 'AdGuard password: ' password; echo
htpasswd -bnBC 12 '' "$password" | tr -d ':\n'; echo
unset password

python3 - <<'PY'
import base64, getpass, hashlib, secrets
password = getpass.getpass("qBittorrent password: ").encode()
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac("sha512", password, salt, 100000)
print("@ByteArray(%s:%s)" % (
    base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))
PY
```

Validate each JSON with its owning consumer and populate the four GitHub
environment secrets. Reconcile `pve` only if topology/access needs work, then
reconcile `tailnet`; if PVE apply reports new public host-key lines, update
`DEPLOY_SSH_KNOWN_HOSTS`. Perform the candidate-commit, out-of-band
`apps-host`/`openclaw-host` sequence above before merging the change. After the
merge, require successful apps and OpenClaw releases and both launcher audits,
then complete and record the one-time retirement in step 7.
