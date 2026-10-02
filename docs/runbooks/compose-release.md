# Homelab Compose deployment and recovery

## Normal architecture

Production retains three LXCs: `docker_apps`, `tailnet`, and `openclaw`. The
application LXC runs one Compose project, `homelab`, from the self-contained
`apps/compose/homelab` package. The package contains its Compose model,
nonsecret configuration, strict secret-bundle materializer, and semantic smoke
test.

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

## Launcher updates

The stable launcher is infrastructure-owned. Before merging a change to
`scripts/ci/release_launcher.py`, check out the exact candidate commit on a
trusted controller and reconcile `apps-host` and `openclaw-host` separately.
Compare `/usr/local/libexec/homelab-release` on each host with the candidate
file's SHA-256 before allowing automatic runtime deployments. The versioned
release engine travels with each runtime package and does not require this
host-first step when the launcher itself is unchanged.

For a new host, follow [bootstrap.md](bootstrap.md). Hosts still running the
pre-simplification stack use the one-time [legacy cutover](legacy-cutover.md).
The engine refuses an externally owned `homelab_proxy` network before stopping
or replacing it; the historical procedure documents that bounded transition.

## Audit and rollback

Run these commands on the corresponding host:

```sh
/usr/local/libexec/homelab-release audit --target apps
/usr/local/libexec/homelab-release rollback --target apps
# On the dedicated OpenClaw host:
/usr/local/libexec/homelab-release audit --target openclaw
/usr/local/libexec/homelab-release rollback --target openclaw
```

`audit` re-materializes and verifies the current release; it is a mutating
recovery operation. `rollback` selects the previous source with current secrets.
The state and runtime-slot markers remain part of the transaction; package
source carries its Compose file, smoke script, and apps preparer/configuration.
The small package `release.json` files retain the exact older engine contract:
a rollback selects the restored release's embedded engine, which may require
those markers for a subsequent rollback. Keep them unchanged while releases
built with the earlier engine remain reachable. The current engine has no
parallel metadata table and validates package identity and executable inputs.
