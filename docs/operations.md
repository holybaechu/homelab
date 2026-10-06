# Operations

Use these commands and workflows after completing [setup](setup.md). For host
rebuilds, lost access, or storage changes, see [recovery and storage](recovery.md).

## Production environment

Configure these secrets in the GitHub `prod` environment:

| Secret | Value |
| --- | --- |
| `APPS_SECRET_BUNDLE` | Versioned apps JSON from the [secrets guide](../secrets/README.md#apps-bundle) |
| `TAILSCALE_AUTH_KEY` | Authentication key for the tailnet host |
| `TS_OAUTH_CLIENT_ID` | Tailscale client ID for the runner connection |
| `TS_AUDIENCE` | Audience for the runner's Tailscale authentication |
| `DEPLOY_SSH_PRIVATE_KEY` | Deployment private key |
| `DEPLOY_SSH_KNOWN_HOSTS` | Independently verified PVE and guest SSH host keys |

Infrastructure jobs generate PVE and tailnet bundles on the runner. The PVE
public identity is derived from the deployment key. Targets come from topology.

The optional `OPERATOR_SSH_PUBLIC_KEY` **environment variable** in `prod` adds
one personal SSH public key alongside the derived deployment key. A trailing
key comment is removed; multiple lines and SSH options are rejected.
The operator private key stays in your password manager or SSH agent.

## Add operator SSH access

1. Create a dedicated Ed25519 SSH key in your password manager and enable it in
   your SSH agent. Copy only its public-key line.
2. In GitHub, open **Settings → Environments → prod → Environment variables**.
   Add `OPERATOR_SSH_PUBLIC_KEY` with that public key as its value.
3. Dispatch `infra.yml` at the revision containing this support with `unit=pve`
   and `pve_mode=plan`. Review the plan before applying; keep the destructive and
   replacement VMID inputs empty.
4. For a key update, dispatch the same revision with `unit=pve`, `pve_mode=apply`,
   and **`pve_access_only=true`**. This retains the preflight but skips changes to
   shared storage and LXC definitions. The existing Proxmox deployment connection
   installs both keys in every managed container and verifies deployment access.
5. Connect as root over the LAN or management tailnet using the host address
   from [topology](../infra/ansible/inventory/prod/topology.json). A container root
   password is not needed for public-key SSH authentication.

Do not replace `DEPLOY_SSH_PRIVATE_KEY` to add personal access: the runner needs
its current private key to reach Proxmox before it can update container keys.
`DEPLOY_SSH_PUBLIC_KEYS` is not an input to this workflow. Reconciliation owns
the containers' entire `authorized_keys` file, so keep the operator variable set
to retain access. Removing it and applying `pve` revokes the operator key while
retaining the deployment key.

## Run a workflow

In GitHub Actions, open the workflow, choose **Run workflow**, and select the
intended revision and inputs.

| Workflow | Use |
| --- | --- |
| [validate.yml](../.github/workflows/validate.yml) | Tests, Compose rendering, and Ansible syntax checks |
| [apps.yml](../.github/workflows/apps.yml) | `operation=deploy` or `operation=sync-secrets` |
| [infra.yml](../.github/workflows/infra.yml) | One `unit`: `pve`, `tailnet`, or `apps-host` |

Production jobs share the `prod-control-plane` queue. Pending runs are retained
and running jobs are not cancelled. Validation runs separately without production
credentials.

## Deploy apps

1. Update and [validate the application package](../apps/compose/homelab/README.md#making-changes).
2. Merge to `main` to trigger `apps.yml` for its watched paths, or dispatch it
   with `operation=deploy` at the intended revision.
3. Check the result. If activation fails, read the reported failure stage before
   retrying.

Automatic deployments reject watched inputs superseded by current `main`.
Manual dispatch allows an intentional older revision.

Each release contains one exact commit, its engine, and a topology snapshot.
The launcher verifies the archive checksum and engine digest. The engine
prepares the inactive runtime slot with current credentials, validates Compose
and image pins, and activates with `--no-build`. Health and application smoke
checks must pass before the candidate becomes current.

## Rotate credentials

For apps:

1. Replace `APPS_SECRET_BUNDLE` in the `prod` environment.
2. Dispatch `apps.yml` with `operation=sync-secrets`.
3. Check the result and confirm the affected application's login or connection.

Secret sync installs the bundle atomically and recreates the current release
without a new archive, image build, or image pull.

For tailnet, update `TAILSCALE_AUTH_KEY` and dispatch `infra.yml` with
`unit=tailnet`. Bundle formats and password hashes are in the
[secrets guide](../secrets/README.md).

## Reconcile a host

Dispatch `infra.yml` with one unit. For `pve`, use `pve_mode=plan` first,
review the result, then select `audit` to check drift or `apply` to make changes.

Leave `allow_destructive_vmid` and `allow_replacement_vmid` empty for routine
runs. Exact VMID confirmations follow the
[infrastructure rules](../infra/README.md#check-pve-changes).

After PVE apply, guest keys read through trusted `pct` must match
`DEPLOY_SSH_KNOWN_HOSTS`. If the job reports a mismatch, update the secret with
its verified public lines before another host reconcile or deployment. Collect
any missing key through the trusted PVE console after a partial apply.

The daily schedule runs at 03:17 Asia/Seoul. It reconciles `tailnet` and
`apps-host` sequentially, upgrades packages, and verifies any required reboot.

## Audit and rollback

Run as root on `docker_apps`. Audit regenerates configuration and recreates the
current release, so plan for a service interruption:

```sh
/usr/local/libexec/homelab-release audit --target apps
```

To reactivate the recorded previous release:

```sh
/usr/local/libexec/homelab-release rollback --target apps
```

Rollback requires current and previous releases. It restores code and
configuration using the **current** secret bundle. Application data and old
credentials are not restored.

## Failed or interrupted operations

Deploy, secret sync, audit, and rollback share one host lock. Each operation
resolves an interrupted transaction before beginning new work.

If activation fails, the engine restores the previously active release. A failed
first deployment has no previous release: the candidate is stopped and no current
release is recorded. Correct the reported cause, then rerun deployment.

| Reported problem | What to check |
| --- | --- |
| Network ownership | `homelab_proxy` must have Compose ownership for project `homelab` and network `proxy` |
| Insufficient space | Root-storage headroom; follow [storage maintenance](recovery.md#grow-an-lxc-root-disk) |
| Compose or image verification | Configuration and image pins at the selected commit |
| Health or smoke | The service, DNS, ingress, and current credentials |

The CLI reports the failure stage and exit status after attempted recovery.
Raw captured command output stays private.

## Release files and capacity

| Host path | Contents |
| --- | --- |
| `/opt/homelab/compose-releases` | Immutable source and embedded engines |
| `/opt/homelab/compose-runtime` | Runtime slots with private generated configuration |
| `/opt/homelab/compose-control` | Transaction state and release records |

Leave release files, state, and runtime markers intact. The engine keeps source
and image references needed by `current`, `previous`, and interrupted `pending`
records. Cleanup considers only managed images and protects images used by other
containers; failed cleanup is retried later.

Image pulls require at least 4 GiB free on the release filesystem. Deployment
retains durable mounts and named volumes and never stops Compose with
`--volumes`. Keep independent [data backups](recovery.md#what-to-back-up).

## Launcher updates

Before merging a change to
[scripts/ci/release_launcher.py](../scripts/ci/release_launcher.py):

1. Check out the exact candidate commit on a trusted controller with verified
   SSH access.
2. Reconcile `apps-host` from that checkout using the
   [host configuration command](setup.md#configure-the-hosts).
3. Compare the installed `/usr/local/libexec/homelab-release` SHA-256 with the
   candidate file. Resolve any mismatch.
4. Merge after the launcher is installed and verified.

The engine travels inside the app package; changing the engine alone does not
require a launcher installation.

## Workflow dependencies

Pin actions by full commit SHA with a readable version comment for Renovate.
Runner Linux Tailscale updates use `TarballsVersion` from the
[stable package feed](https://pkgs.tailscale.com/stable/?mode=json), through
`custom.tailscale-linux`. The action verifies the publisher's checksum and fails
if the artifact or checksum is missing.
