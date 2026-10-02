# GitHub Actions deployment lanes

The repository has three workflows: validation, targeted infrastructure, and
apps. All production mutations share the non-cancelling `prod-control-plane`
queue; validation is independent.

## Validation

`validate.yml` runs on pull requests, merge queues and manual requests. It
installs `requirements-dev.txt` and `requirements-deploy.txt`, tests behavior
and safety contracts, renders the apps package once, and syntax-checks every
infrastructure unit. It has read-only repository permissions and no production environment.

## Apps

`apps.yml` checks out the exact source, runs package/release tests, renders
Compose, and bundles the complete `apps/compose/homelab` package with its
embedded engine and topology snapshot. The runner materializes one temporary
`APPS_SECRET_BUNDLE`, joins tailnet, establishes pinned SSH trust, uploads once,
and calls the stable host launcher. It does not reconcile infrastructure.

Before automatic mutation, the lane compares its input paths with current
`main` and refuses superseded inputs. Manual dispatch permits an intentional
older release. `operation=sync-secrets` uploads only the component document and
recreates the installed release without a new archive or image pull.

## Infrastructure

`infra.yml` accepts exactly one unit: `pve`, `tailnet`, or `apps-host`. PVE
chooses `plan`, `audit`, or `apply`; destructive/replacement changes require
exact VMID inputs. The daily schedule reconciles tailnet and apps-host
sequentially, with explicit package upgrades and marker-gated reboot recovery.

PVE apply installs LXC access and compares keys read through trusted `pct` with
`DEPLOY_SSH_KNOWN_HOSTS`. If keys differ, the job reports the verified public
lines and fails until the production trust secret is updated. PVE and tailnet
receive only their respective component documents. Apps-host provisions host
primitives, including the launcher, while the apps lane owns activation.

## Runner Tailscale version

Runner Linux builds are pinned to a version published at
[the stable package feed](https://pkgs.tailscale.com/stable/?mode=json).
Renovate reads `TarballsVersion` through `custom.tailscale-linux`; platform-wide
GitHub tags are unsuitable because they may have no Linux tarball. The action
still verifies the publisher's checksum. A missing checksum/artifact fails
before a production connection rather than silently selecting another build.

## Production environment contract

Store `APPS_SECRET_BUNDLE` and `TAILSCALE_AUTH_KEY`. The infrastructure job
renders versioned PVE/tailnet JSON on the runner: the PVE public key is derived
from the configured SSH identity. Connection credentials: `TS_OAUTH_CLIENT_ID`,
`TS_AUDIENCE`, `DEPLOY_SSH_PRIVATE_KEY`, and `DEPLOY_SSH_KNOWN_HOSTS`.

Host targets come only from topology. Actions use full commit pins with readable
version comments. Runtime jobs use `contents: read` and `id-token: write` for
the tailnet connection. `queue: max` retains pending desired-state runs.

## Host commands

```sh
/usr/local/libexec/homelab-release audit --target apps
/usr/local/libexec/homelab-release rollback --target apps
```

Audit re-materializes and verifies current state; it is a mutating recovery
operation. Normal credential rotations use the manual apps workflow.
