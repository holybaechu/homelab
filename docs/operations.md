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
2. Merge to `main`, then manually dispatch `apps.yml` with `operation=deploy`.
   Merging or accepting a dependency update does not deploy production.
3. Check the workflow result and application access.

The selected commit is copied to `/opt/homelab/compose` and prepared with the
private version-2 apps bundle. Native Compose pulls pinned images, recreates
services with changed images or environment, and waits for health. Mounted
configuration changes recreate only affected services; Traefik watches its
dynamic directory. Read-only checks cover DNS, HTTPS, public Copyparty reads,
protected redirects, OIDC discovery, and Headscale. The blueprint is applied
when identity configuration or its images change.

The first deployment migrates the old runtime mounts and recreates all containers
once. It keeps the project, named volumes, durable mounts, credentials, and
Tailscale access. It saves previous configuration, an Authentik SQL dump, and an
offline Headscale volume copy first. If activation fails, it reactivates the
untouched old installation. No VM migration is involved.

## Deploy identity services

This package adds Authentik at `auth.home.hchu.me`, Headscale at
`headscale.home.hchu.me`, and an Authentik login gate at `metube.home.hchu.me`.
Immich and Minecraft are not included.

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

## Copyparty and Proxmox identity

Deploy the application package first. Its blueprint declares the Copyparty proxy
provider, Proxmox OIDC provider, access groups, policies, and embedded outpost.
These settings are reconciled when identity configuration or images change; edit the blueprint
instead of changing its managed objects only in the UI.

Copyparty browser login uses Authentik. Grant `copyparty-users` for read access to
the shared read-only area, or `copyparty-admins` for management and public-share
write access.
The read-only downloads and shared mounts remain read-only. Anonymous GET/HEAD
requests to `/public` stay available; writes require an authorized account.
Authenticated visitors use the ordinary SSO route, including `/public`.
Copyparty's existing DNS CNAME points to the DDNS-managed `home.hchu.me`; retain
that alias at the DNS provider rather than adding a conflicting A record.
Only Traefik and Copyparty join its private proxy network, and the edge removes
client-supplied identity headers before authenticating them.

WebDAV/rclone clients use `https://copyparty-dav.home.hchu.me` from the LAN or
management VPN, with their existing Copyparty credentials. The private route
removes identity headers and uses native client authentication. It requires no
public DNS record; AdGuard's existing wildcard resolves it on the LAN.

For Proxmox, dispatch `infra.yml` with **unit=pve**, **pve_mode=apply**, and
**pve_identity_only=true**. Leave `pve_access_only` false. This reconciles only
the additional `authentik` OIDC realm and administrator grants; it skips storage,
LXC definitions, and SSH access. The hosted job generates a private version-2
PVE bundle from the existing apps bundle and deployment public keys.

The Proxmox client credential is a purpose-specific HMAC-SHA256 derivation from
the stable Authentik secret key, with context `homelab/proxmox-oidc/v1`. The apps
preparer and PVE workflow produce the same value without storing it in Git.
Changing that master key requires both an apps credential sync and another PVE
identity reconciliation.

Declare allowed Proxmox administrator usernames in `pve_identity_administrators`
in [group variables](../infra/ansible/inventory/prod/group_vars/all.yml), and grant
them `homelab-admins` in Authentik. The initial administrator is `akadmin`.
Removing a declared username revokes its managed root-level `PVEAdmin` grant.
The realm does not automatically create other users or become the default.

PVE's Linux PAM realm and local `root@pam` account are retained. Test a local
administrator login and keep its credentials in your password manager. During
an Authentik outage, select Linux PAM at the login screen. If the apps host is
also unavailable, open the PVE topology address directly on port 8006 from the
LAN or a working management VPN.

## What identity IaC reproduces

| Service | Recreated from the repository and matching private bundles | State that needs backups |
| --- | --- | --- |
| Authentik | Pinned services, database connection, initial administrator, named groups, providers, application policies, and embedded outpost configuration | Ordinary users, passwords changed after bootstrap, MFA, sessions, signing keys, uploaded data, and UI-only changes |
| MeTube | Service settings, mounts, routing, and Authentik login policy | Downloads and application data |
| Headscale | Service, OIDC settings, policy, route approvals, and HTTPS endpoint | Registered users/devices, registration/API keys, SQLite database, and control keys |
| Copyparty | Services, shares, native client accounts, proxy identity mapping, group permissions, and public/client routes | Files and persistent indexes |
| Proxmox | Additional OIDC realm, declared SSO administrators, and managed administrator grants | Existing local users, recovery credentials, unrelated permissions, and host/guest data |

Apply the three infrastructure units separately, deploy apps, then apply PVE
identity after its OIDC discovery endpoint is ready. A fresh deployment initializes
the declared configuration; restoring existing identities and device registrations
also requires the [private data backups](recovery.md#what-to-back-up).
Headscale's root page is minimal because no web dashboard is included.

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
2. On the apps host, change into `/opt/homelab/compose`, then define a CLI
   shortcut and create the service users:

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

Secret sync validates the bundle before installing it and uses the currently
installed package. Compose updates affected environments and configuration;
unchanged services keep running. Bootstrap fields initialize new installations
only. Database password changes require the private database operation described
in the [secrets guide](../secrets/README.md).

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

The weekly schedule runs Sunday at 03:17 Asia/Seoul. It reconciles `tailnet`
and `apps-host` sequentially, upgrades host packages, reboots only when required,
and reads service health. It does not redeploy or recreate applications.
Renovate groups routine dependency updates weekly; merging and deployment are
manual. Major upgrades remain separate for reviewing their migration instructions.

## Audit and rollback

Read health without changing services, as root on `docker_apps`:

```sh
cd /opt/homelab/compose
docker compose --project-name homelab ps --all
sh smoke.sh
```

Before each deployment, `/opt/homelab/compose-previous` holds one private copy
of the installed configuration and component bundle. A repeat deployment replaces
that copy. Save it elsewhere before retries when you need an older recovery point.
Retained images are not automatically pruned.

For backups made by the stable deployment, copy previous configuration to a
private temporary directory first (deployment
replaces `compose-previous`), then use the **current** deployer:

```sh
recovery="$(mktemp -d)"
cp -a /opt/homelab/compose-previous/. "$recovery/"
python3 /opt/homelab/compose/deploy.py --source "$recovery" \
  --secret-bundle "$recovery/apps.json" --revision recovery
rm -rf -- "$recovery"
```

The first migration's previous copy uses the old mount layout. To return to it,
read `active_slot` from `/opt/homelab/compose-control/release-state.json`, change
into `/opt/homelab/compose-runtime/<active_slot>/stack`, and run
`docker compose --project-name homelab up -d --no-build --pull never --force-recreate --wait`.
This uses the retained old configuration directly. Hold deployment workflows
while recovering and remove the stable `.revision` marker before retrying the
migration. Do not use the retired launcher.

Use this for compatible configuration or stateless image changes. Authentik
downgrades require restoring a matching database backup; PostgreSQL major changes
need their own migration. Restore Headscale data with its matching version.
Keep independent backups outside this LXC; local SQL and Headscale copies under
`/opt/homelab/backups` protect migration and identity image upgrades only.

## Failed or interrupted operations

The deployer validates credentials and Compose and pulls images before modifying
the installed package. Once activation begins, failure leaves the attempted
configuration in the stable directory. Correct it and redeploy, or follow the
manual recovery procedure above. Routine deployments do not automatically undo
application databases or images. The first migration has an old-install fallback.

Deployments share a host lock and production workflows share a queue. Inspect
`docker compose ps --all` and failing service logs from a trusted console. Keep
logs private; they may contain application credentials or login data. The workflow
reports the failed operation without printing Compose environments.

## Release files and capacity

| Host path | Contents |
| --- | --- |
| `/opt/homelab/compose` | Stable source, topology snapshot, private generated configuration, and `.revision` |
| `/opt/homelab/compose-previous` | Previous configuration and private apps bundle |
| `/opt/homelab/backups` | Private identity data backups before migration or identity image changes |
| `/etc/homelab/secrets/apps.json` | Current private version-2 bundle |

Former `compose-releases`, `compose-runtime`, and `compose-control` directories
are left untouched after migration. Keep them until access is verified and
independent backups are copied; they contain private files. Their launcher is
retired. Routine deployment never removes named volumes or durable app files.
Check disk space before large updates and follow
[storage maintenance](recovery.md#storage-maintenance) when expanding the LXC.

## Workflow dependencies

Pin actions by full commit SHA with a readable version comment for Renovate.
Runner Linux Tailscale updates use `TarballsVersion` from the
[stable package feed](https://pkgs.tailscale.com/stable/?mode=json), through
`custom.tailscale-linux`. The action verifies the publisher's checksum and fails
if the artifact or checksum is missing.
