# GitHub Actions CI/CD

`.github/workflows/ci.yml` is the repository's only workflow. It classifies an
exact revision, validates that same `github.sha`, and only then permits a
single selected production release transaction. Pull requests and scheduled runs never create a
runtime deployment plan. Unknown paths below `apps/`, `infra/`, or
`scripts/ci/` fail closed instead of triggering a broad fallback.

## Fast validation

The classifier emits one validation scope before tests begin:

- `apps-model`: the 18 policy/config/ingress tests for an ordinary
  `apps/compose/homelab` edit, followed by real Compose rendering;
- `apps`: the model checks plus direct uploader, locked deployer, and immutable
  T3 image tests when deployment tooling or image inputs change;
- `openclaw`: immutable two-image release, deterministic bundle, direct SSH,
  rollback, host-boundary, and skill-promotion tests;
- `repo`: routing and workflow contract tests; or
- `full`: the complete repository suite, OpenTofu validation, every active
  Ansible playbook syntax check, and Compose validation.

Only `full` installs Ansible dependencies, Ansible Galaxy collections, and
OpenTofu. The daily schedule always selects `full`. Ordinary app or OpenClaw
runtime changes therefore validate only the deployable unit instead of
repeating unrelated infrastructure work.

## Deployment components

Changed paths map to the deterministic component order
`tofu,bootstrap,tailnet,openclaw,apps`. Mixed changes take the union.
Documentation and tests do not deploy. `apps` always means the one `homelab`
project; the retired `platform`, `media`, `code`, Arcane, and Docker OpenClaw
projects are not selectable.

`ADOPTION_RETIREMENT_PATHS` is a temporary exact list of strict-tree files
deleted by this cutover. It gives the first `--no-renames` push empty ownership
instead of reviving retired components. Remove the tombstones after the
cutover commit is the deployment diff base everywhere.

The post-validation `prod_mutation` job is one release-level transaction under
the global `prod-mutation` concurrency group. It waits for both exact-SHA image
build jobs, prepares only the selected component inputs, checks
`origin/main == GITHUB_SHA` once immediately before the mutation sequence, and
then applies selected components in this order:

1. OpenTofu;
2. bootstrap or isolated tailnet reconciliation;
3. exact OpenClaw release; and
4. the one homelab Compose release.

Skipped component steps run no deployment setup. A push with no deployable
paths runs only the exact checkout, latest-main watermark gate, and GitHub API
watermark write; manual validation-only dispatches do not enter the production
job. OpenClaw precedes apps in a mixed release, so the Gateway is ready before
the proxy stack activates. Host deployers retain their nonblocking kernel
locks for manual or out-of-band collision protection.

GitHub may replace an older pending member of a concurrency group. Therefore
the workflow never uses an individual push's `before` SHA as its release base.
Classification queries the newest successful `prod-release` GitHub deployment
whose exact SHA is an ancestor of the current SHA, then diffs that watermark to
the current revision with `--no-renames`. After every automatic push release
succeeds—including a release with no production mutations—the locked job
rechecks `origin/main == GITHUB_SHA`, creates a `prod-release` deployment for
that exact SHA, and records a successful deployment status. If an intermediate
run is coalesced or becomes stale, the next retained run still selects the full
change range since the last complete release and cannot drop its changes.

## App release path

The app step inside the common release transaction runs exactly:

```sh
sh scripts/ci/deploy-compose-via-ssh.sh "$GITHUB_SHA"
```

It uploads only `apps/compose/homelab`, reads prepared inputs from
`/etc/homelab/runtime`, and invokes the locked remote deployer. It installs no
Python runtime, Ansible, Galaxy collection, or OpenTofu on the runner and runs
no broad live validation. If a T3 image input changed, `t3_build` builds and
approves the exact same-build digest first, then passes the paired
`T3_IMAGE_REF` and `T3_SOURCE_SHA`; otherwise the remote trusted approval is
reused.

## OpenClaw release path

An OpenClaw release is one canonical state:

- exact Gateway and CTF `repository@sha256:<64 hex>` references;
- deterministic runtime bundle SHA-256;
- exact private-config commit and deterministic bundle SHA-256; and
- exact homelab deployment SHA.

`openclaw_build` builds changed image inputs from digest-pinned bases and uses
Buildx's same-build metadata to approve the resulting digest. The selected
OpenClaw steps check out the private `holybaechu/openclaw-setup` commit with
credentials disabled after checkout, reproduces both bundles, creates the
canonical manifest, and runs:

```sh
sh scripts/ci/deploy-openclaw-via-ssh.sh \
  "$GITHUB_SHA" release.json runtime.tar config.tar
```

This common promotion path uses the preinstalled deployer on the dedicated
OpenClaw LXC. It does not install Python, Ansible, Galaxy, OpenTofu, Node/npm,
or build an image. The host deployer verifies the exact digests, runs Compose
with `--no-build`, waits on `/readyz`, makes one authenticated smoke request,
and rolls back both images and config to the previous release on failure. The
SSH client then runs the non-authenticated audit.

The retired runtime export described in `docs/runbooks/openclaw.md` stays in
offline recovery storage. It is not a workflow secret, deployment input, or
live state record.

An autonomous skill promotion sends `repository_dispatch` type
`openclaw-promoted` with its exact private-config commit. The workflow combines
that commit with the configured current exact image/runtime identities,
recomputes the deterministic config hash from the exact checkout, and selects
only `openclaw`. The skill-sync GitHub credential remains in its isolated host
service and is never mounted into the Gateway.

## Manual dispatch

`workflow_dispatch` requires an explicit `components` CSV. Empty means
validation only; it never means deploy everything. Names must be unique and a
subset of `tofu,bootstrap,tailnet,openclaw,apps`. Selecting `apps` implicitly
selects the sole `homelab` project. Selecting `openclaw` also requires all five
exact release inputs:

- `openclaw_config_commit`
- `openclaw_gateway_ref`
- `openclaw_ctf_ref`
- `openclaw_runtime_sha256`
- `openclaw_config_sha256`

`tofu_force_unlock_id` is a manual diagnostic for a confirmed stale lock in
the remote state. Leave it empty during ordinary deployment.

## Provisioning and maintenance

`bootstrap` owns OS/Docker prerequisites, firewall boundaries, root-owned
runtime and secret materialization, and installation of the opaque host
deployers. It writes the combined `tailnet,openclaw,apps` schema only in the
provisioning lane, then reconciles `site.yml`. An isolated tailnet selection
writes and exposes only the tailnet input. Production inventory is the single
`infra/ansible/inventory/prod/topology.json` file.

The schedule does not run the runtime plan. After exhaustive repository
validation it runs `maintenance.yml` under `prod-mutation`, waits for controlled
restart/reboot recovery, and runs the complete live `validate.yml`. Routine
reconciliation keeps apt packages present; only maintenance enables upgrades.

## `prod` environment configuration

Connection and provisioning variables:

- `PVE_NODE_NAME`, `PVE_BRIDGE`, `PVE_ROOT_DATASTORE_ID`
- `PVE_TAILSCALE_IP`, optional `DOCKER_APPS_IP`, optional `OPENCLAW_IP`
- `TOFU_STATE_BUCKET`, `TOFU_STATE_KEY`, `TOFU_STATE_REGION`,
  `TOFU_STATE_ENDPOINT`
- optional `ADGUARD_ADMIN_USERNAME`

Exact OpenClaw defaults used for source-path and autonomous promotions:

- `OPENCLAW_CONFIG_COMMIT`, `OPENCLAW_CONFIG_SHA256`
- `OPENCLAW_GATEWAY_REF`, `OPENCLAW_CTF_REF`, `OPENCLAW_RUNTIME_SHA256`
- digest-pinned build bases `OPENCLAW_GATEWAY_BASE_REF`,
  `OPENCLAW_PYTHON_BASE_REF`, `OPENCLAW_DOCKER_CLI_REF`,
  `OPENCLAW_CTF_BASE_REF`, and `OPENCLAW_UV_BASE_REF`

Shared/provisioning secrets include `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`,
`DEPLOY_SSH_PRIVATE_KEY`, `DEPLOY_SSH_KNOWN_HOSTS`, Proxmox/OpenTofu state
credentials, and `DEPLOY_SSH_PUBLIC_KEYS`. Bootstrap additionally receives the
component-scoped secrets defined by `infra/deployment/secrets.json`. The direct
OpenClaw path receives only `OPENCLAW_CONFIG_READ_TOKEN`; the direct apps path
receives no application secret values.

`DEPLOY_SSH_KNOWN_HOSTS` must contain pinned entries for Proxmox and the three
LXCs. Never discover keys inside a deployment job. Job-level `id-token: write`
and `deployments: write` exist only on the release job that connects to
Tailscale and records `prod-release`; plan, validation, and image build jobs
retain minimal read/package permissions.
