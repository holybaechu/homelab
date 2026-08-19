# Homelab Compose deployment and recovery

## Normal architecture

Production retains three LXCs: `docker_apps`, `tailnet`, and `openclaw`.
The application LXC has one Compose project, `homelab`. Ansible prepares
root-owned runtime inputs; it does not build, pull, or activate application
containers. CI uploads one exact-commit release, and the locked direct deployer
validates, pulls, health-checks, and activates it.

T3 Code is built and published in CI from `apps/images/t3code/Dockerfile`.
Production receives only an exact
`ghcr.io/holybaechu/homelab-t3code@sha256:<64hex>` reference recorded in the
release metadata. The host never receives the Dockerfile or a build context.
An unrelated homelab release reuses the last approved digest. A changed T3
image is promoted only after the same tested source SHA passes activation and
health checks. Ordinary rollback activates the previous homelab release and
therefore its previously recorded digest.

## Rollback behavior

Routine rollback always restores the previous `homelab` release and the exact
T3 digest stored with that release. The active deployment path contains no
legacy-project selector, cutover marker, or split-stack rollback branch.

The deployer records one durable `good`/`previous` state plus a phase journal.
An interrupted command reconciles that journal before another deploy or
rollback, so the current symlink, running Compose project, state, and T3 digest
converge to the same release.

## Reconstruction

Rebuild the `docker_apps` LXC through the topology/OpenTofu and targeted
Ansible bootstrap paths. Those steps recreate the Docker host, persistent
mounts, root-owned secrets, generated private configuration, and mandatory
smoke contract. Then run the normal exact-SHA uploader. Initial activation has
no previous release; a failure stops the candidate and leaves no current
release. No special marker or alternate deployment state machine is used.

The source-only `scripts/recovery/compose_stack_cutover.py` is retained solely
as an offline, manually invoked record of the already-tested split-stack
cutover. It is not shipped in application releases, selected by routine CI, or
consulted by production validation. Archive it with the old split-stack
backup after the production cutover record is complete.
