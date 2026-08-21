# GitHub Actions deployment lanes

The repository has four coarse workflows. Each production lane starts from a
complete desired state and does not read another workflow run or a previous
GitHub deployment record.

## Validation

`validate.yml` runs on pull requests, merge queues, and manual requests. One
exact checkout runs the complete behavioral/invariant test suite, renders the
apps and OpenClaw Compose packages, and syntax-checks `reconcile.yml` for all
four infrastructure units. It has read-only repository permission and no
production environment.

## Apps

`apps.yml` is the ordinary application path. A change below
`apps/compose/homelab` runs in one job and one checkout:

1. run package and common release-transaction tests;
2. render the apps Compose package;
3. bundle the complete apps package with the exact homelab commit;
4. materialize the single `APPS_SECRET_BUNDLE` runner file;
5. join the management tailnet and configure pinned SSH trust; and
6. upload the release plus component bundle once and invoke the stable host
   launcher.

The lane never calls Ansible or an image build. Manual dispatch defaults to the
same complete deployment; selecting `sync-secrets` uploads only the component
document and recreates the current release through the installed engine.

## OpenClaw

`openclaw.yml` runs for public runtime/image changes, a private-config
promotion dispatch, or a manual request. Gateway and CTF jobs build in
parallel from Dockerfiles whose `FROM` lines contain exact digests. The pinned
Buildx actions use independent GitHub Actions caches, publish the two images,
attach maximum provenance and SBOM attestations, and return exact OCI digests.

The deploy job checks out the exact homelab source and either an explicitly
promoted private-config commit or current private `main`. It records the exact
resolved commit and builds one descriptor containing:

- homelab commit;
- private-config commit;
- Gateway `repository@sha256` identity; and
- CTF `repository@sha256` identity.

The bundle checksum proves only upload integrity. It is not another desired
state input. The job sends that bundle and `OPENCLAW_SECRET_BUNDLE` through the
same release wrapper used by apps.

Manual dispatch defaults to the complete descriptor path. Selecting
`sync-secrets` skips both image jobs, bundle construction, and image pulls; it
uploads only `OPENCLAW_SECRET_BUNDLE` and recreates the already-current release
under the same host lock and semantic smoke gate.

Live skill collection is not a production-host service. The private
`holybaechu/openclaw-setup` repository runs its pinned scheduled workflow,
snapshots the two bounded skill roots over read-only SSH, validates a
content-derived pull request, merges it, then dispatches the exact resulting
private-config commit to this lane.

## Infrastructure

`infra.yml` exposes exactly four manual units:

- `pve`
- `tailnet`
- `apps-host`
- `openclaw-host`

Every invocation passes one required `homelab_unit` to the sole Ansible
entrypoint, `infra/ansible/playbooks/reconcile.yml`. PVE additionally chooses
`plan`, `audit`, or `apply`; potentially destructive or replacement changes
remain rejected unless the matching exact VMID is supplied in the manual
approval input. The daily schedule runs the three non-PVE units sequentially
with targeted package upgrades and marker-gated reboot handling.

After a PVE apply, the job compares every host key read through trusted `pct`
against the supplied `DEPLOY_SSH_KNOWN_HOSTS`. A new or replaced LXC key fails
the job after reconciliation and writes the exact verified public lines to the
job summary. Update that production environment secret before another host or
runtime job; this makes the rare trust handoff explicit instead of leaving the
next deployment with a silent stale-key failure.

PVE and tailnet receive only their versioned component bundle. Apps/OpenClaw
host units create OS, Docker, firewall, storage, account, release-root, and
launcher primitives; application activation remains in the runtime lanes.

## Production environment contract

Component documents:

- `APPS_SECRET_BUNDLE`
- `OPENCLAW_SECRET_BUNDLE`
- `PVE_SECRET_BUNDLE`
- `TAILNET_SECRET_BUNDLE`

Connection credentials:

- `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`
- `DEPLOY_SSH_PRIVATE_KEY`, `DEPLOY_SSH_KNOWN_HOSTS`
- `OPENCLAW_CONFIG_READ_SSH_KEY`

Host addresses are read only from
`infra/ansible/inventory/prod/topology.json`. Workflows do not provide address
overrides. All actions use immutable commit pins, runtime jobs receive only
`contents: read` plus the narrow write/OIDC permission they consume. All
production mutations share one non-cancelling concurrency group so a host
package/reboot or tailnet restart cannot interrupt a release transaction;
`queue: max` preserves every waiting desired-state run instead of replacing a
pending deployment. Validation remains independent. The two OpenClaw image
builds run in parallel inside the admitted OpenClaw workflow.

## Operator commands

The stable host launcher is `/usr/local/libexec/homelab-release`:

```sh
/usr/local/libexec/homelab-release audit --target apps
/usr/local/libexec/homelab-release rollback --target apps
/usr/local/libexec/homelab-release audit --target openclaw
/usr/local/libexec/homelab-release rollback --target openclaw
```

`sync-secrets` accepts one already uploaded component JSON file, atomically
installs it, and recreates the current release. Normal rotations use the manual
runtime workflow so validation, transport, smoke, and cleanup remain uniform.
