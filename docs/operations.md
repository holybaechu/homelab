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
| `HEADSCALE_CI_AUTH_KEY` | Optional reusable, ephemeral Headscale registration key tagged `tag:ci`; enables the runner cutover |
| `HEADSCALE_GATEWAY_AUTH_KEY` | Optional Headscale registration key tagged `tag:gateway`; enables gateway reconciliation against Headscale |
| `DEPLOY_SSH_PRIVATE_KEY` | Deployment private key |
| `DEPLOY_SSH_KNOWN_HOSTS` | Independently verified PVE and guest SSH host keys |

Infrastructure jobs generate PVE and tailnet bundles on the runner. The PVE
public identity is derived from the deployment key. Targets come from topology.
Keep the Headscale secrets unset during bootstrap. The workflows then retain
their hosted Tailscale connection. Never set the gateway key before migrating
the gateway from a trusted LAN controller or PVE console.

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

## Deploy identity services

This package adds Authentik at `auth.home.hchu.me`, Headscale at
`headscale.home.hchu.me`, and an Authentik login gate at `metube.home.hchu.me`.
Immich and Minecraft are not included.

For an existing installation, deploy rollback compatibility before adding the
identity services. Create and deploy a separate exact revision containing only
the `compose_release_engine.py` secret-compatibility changes and their tests,
keeping the old Compose package and version-1 preparer. In that revision, retain
`secret_bundle.version: 1` in `release.json` and add
`secret_bundle.compatible_versions: [1, 2]`. Deploy it with the existing version-1
apps bundle, then verify the release. The full identity release refuses to
replace credentials until the current release declares this compatibility.
An empty installation can start directly with the version-2 package.
After installing version-2 credentials, rollback is limited to the compatibility
release or later revisions; an older package is rejected before activation.

1. Back up existing application data and credentials. Prepare the version-2
   [apps bundle](../secrets/README.md#apps-bundle), preserving current application
   credentials. Keep both Headscale environment secrets unset during bootstrap.
2. The package's Cloudflare DDNS updater creates and maintains DNS-only A records
   for the three hostnames above and `home.hchu.me`. Keep any existing records
   DNS-only, including Headscale. Forward TCP 443 to the apps host from topology.
   Traefik already uses DNS challenges for certificates, so inbound TCP 80 is
   optional.
3. Replace `APPS_SECRET_BUNDLE` and deploy the complete identity package using the
   [apps workflow](#deploy-apps). Its blueprint initializes the Headscale OIDC
   provider and MeTube proxy provider with the embedded outpost; no Docker socket
   is exposed to Authentik. Smoke checks require OIDC discovery, Headscale health,
   and an unauthenticated MeTube redirect to the login flow.
4. Sign into Authentik as `akadmin` with the bundle's bootstrap password. Configure
   MFA, create individual users, and grant `metube-users` to people allowed to use
   MeTube. Add only management users to `homelab-admins`; that group may register
   VPN devices with access to the LAN. MeTube still has one shared application
   state and download area.
5. From a test device outside the LAN, verify MeTube requires login and an
   authorized user can open it. Verify the authentication callback remains
   reachable and that a user without `metube-users` is denied. Test from the LAN
   too; existing AdGuard rewrites route the hostnames directly to the apps host.

The blueprint owns its named providers, applications, bindings, embedded outpost,
and `akadmin` group membership. Edit the blueprint for those settings; manage
ordinary users and their group membership in Authentik. Do not attach forward
auth to Authentik itself or Headscale's control endpoint. Keep PVE, router,
AdGuard, and qBittorrent on their existing private routes.
Identity and proxy callback routers omit Traefik access logs so authorization
codes and login state are not recorded there.

## Move management access to Headscale

Run the cutover from a trusted LAN controller with PVE console access. Hosted
jobs cannot restore their own route if the new Headscale service is unreachable.
Headscale and Authentik live on the apps host, so recovery of that host requires
LAN or PVE console access. The existing hosted Tailscale path remains selected
until its corresponding Headscale secret is set.

1. Register a test management device using the Tailscale client and
   `--login-server=https://headscale.home.hchu.me`. Sign in through Authentik with
   a `homelab-admins` user. Verify registration from outside the LAN. Headscale
   initially uses the public Tailscale DERP map; no extra relay or UDP port is
   required for this deployment.
2. On the apps host, locate the current runtime's stack directory through the
   recorded active slot in `/opt/homelab/compose-control/release-state.json`.
   Change into that stack directory, then define a CLI shortcut and create the
   service users:

   ```sh
   hs() {
     docker compose --project-name homelab -f compose.yml exec -T \
       headscale headscale --config /etc/headscale/config.yaml "$@"
   }
   hs users list
   hs users create gateway
   hs users create ci
   ```

   Record the resulting numeric user IDs.
3. Generate a reusable gateway pre-authentication key tagged `tag:gateway`, and a
   reusable, ephemeral CI key tagged `tag:ci`, using
   `hs preauthkeys create --user USER_ID --reusable --tags TAG --expiration 90d` (add `--ephemeral` for
   CI). Run these only on a trusted console, save their output directly into
   private bundles/environment secrets, and rotate them before expiry. These
   are device keys, not API keys.
4. Prepare a [version-2 tailnet bundle](../secrets/README.md#infrastructure-bundles).
   On the gateway through the LAN or PVE console, run `tailscale logout`; this
   interrupts its old management connection. From the LAN controller, reconcile
   only `tailnet` with the new bundle using the [setup command](setup.md#configure-the-hosts).
   The role refuses to change an active control server without this logout.
5. On the test management device, accept the advertised subnet route and verify
   SSH to all managed hosts from topology. The policy automatically approves the
   gateway's subnet and exit-node routes. Test exit-node access separately if used.
6. Set `HEADSCALE_GATEWAY_AUTH_KEY` and `HEADSCALE_CI_AUTH_KEY` in `prod`, then
   dispatch `infra.yml` for `tailnet` and an apps workflow. Confirm the runners
   reach their selected hosts through Headscale before migrating remaining devices
   or retiring hosted Tailscale credentials. Do not resume scheduled jobs until
   both the gateway and runners use the same network.

Tag ownership belongs to `group:operators` in the package's Headscale policy,
initially `akadmin@`. Add other management usernames there when granting tag
ownership. CI is limited to LAN SSH and receives no general application access.

If cutover fails, stop hosted jobs and use the LAN or PVE console. Log the gateway
out again, reconcile with the original version-1 tailnet bundle, clear both
Headscale environment secrets, and verify the original runner connection before
resuming workflows. Restore or redeploy the identity package from the LAN before
retrying. App rollback does not restore identity databases or their migrations.

## Rotate credentials

For apps:

1. Replace `APPS_SECRET_BUNDLE` in the `prod` environment.
2. Dispatch `apps.yml` with `operation=sync-secrets`.
3. Check the result and confirm the affected application's login or connection.

Secret sync installs the bundle atomically and recreates the current release
without a new archive, image build, or image pull.

For hosted Tailscale, update `TAILSCALE_AUTH_KEY` and dispatch `infra.yml` with
`unit=tailnet`. After cutover, rotate the Headscale registration keys instead:
replace `HEADSCALE_CI_AUTH_KEY` for runners and `HEADSCALE_GATEWAY_AUTH_KEY` for
the gateway, then reconcile `tailnet` against the same control server. Bundle formats and password hashes are in the
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
