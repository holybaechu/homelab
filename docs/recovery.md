# Recovery and storage

For a failed app deployment with working hosts, start with
[release recovery and rollback](operations.md#failed-or-interrupted-operations).
Use this guide to rebuild a host, restore management access, or resize storage.

## What to back up

Keep independent backups outside the affected host:

| Item | Restore purpose |
| --- | --- |
| PVE's `/var/lib/homelab`, mounted in apps as `/srv/homelab` | Application files, downloads, and service state |
| `platform_traefik_data` and `platform_adguard_work` Docker volumes | Certificate and AdGuard work data |
| `platform_authentik_db_pg18`, `platform_authentik_data`, and `platform_headscale_data` Docker volumes | Identity accounts, providers, uploaded files, registered VPN devices, and control keys |
| Private component bundles and connection credentials | Host access and application authentication |
| The intended repository revision | Host declarations and application configuration |

LXC configuration exports under `/var/backups/homelab/pct-config` help review
changes, but they are not data backups. Configuration backups do not restore an earlier copy of application data.

Use a PostgreSQL logical backup for Authentik, or stop its database before copying
the volume. Stop Headscale before copying its SQLite database and private keys.
Keep these backups private and restore the database together with its matching
component credentials. Application rollback preserves these volumes and does not
undo database migrations.

## Upgrade Authentik PostgreSQL

The package supports the PostgreSQL 16-to-18 transition. It restores into
`platform_authentik_db_pg18`, mounted at `/var/lib/postgresql`, and retains
the original `platform_authentik_db` volume. Other major transitions are rejected.

1. Arrange a maintenance window with trusted LAN or PVE console access, hold
   other production workflows, and make an independent private database backup.
   Authentik login is unavailable while its server and worker are stopped.
2. Confirm the installed database is PostgreSQL 16 and the destination volume
   `platform_authentik_db_pg18` does not exist. An existing destination requires
   the recovery steps below; it is never overwritten automatically.
3. Dispatch `apps.yml` with `operation=deploy` at the migration revision.
   The deployer checks the running source version and volume, stops Authentik's
   writers, saves a private custom-format dump, starts only PostgreSQL 18,
   restores atomically, and compares every user table's row count. It then
   activates the complete package and verifies application access.
4. Verify PostgreSQL 18 and Authentik login, users, providers, and applications.
   Keep the original volume, `compose-previous`, and the private migration dump
   under `/opt/homelab/backups/<timestamp>/postgres18` until independent backups
   and recovery are verified. Routine deployment replaces `compose-previous`;
   copy it to private storage before another deployment.

A conversion failure restores PostgreSQL 16 and only the writers that were
running before migration. The failed destination and dump remain for inspection.
If recovery also fails, the deployer reports the retained recovery configuration
path.

### Recover an interrupted conversion

Hold workflows and preserve both volumes, the private dump, and
`/opt/homelab/compose-previous` before retrying. Inspect the running database
and determine whether Authentik has used PostgreSQL 18.

Before any writer has used PostgreSQL 18, restore the retained PostgreSQL 16
configuration from a trusted console:

```sh
recovery=/opt/homelab/compose-previous
docker compose --project-name homelab --project-directory "$recovery" \
  -f "$recovery/compose.yml" up -d --no-deps --no-build --pull never \
  --force-recreate --wait --wait-timeout 300 authentik-db
docker compose --project-name homelab --project-directory "$recovery" \
  -f "$recovery/compose.yml" up -d --no-deps --no-build --pull never \
  --wait --wait-timeout 300 authentik-server authentik-worker
```

Verify PostgreSQL 16, Authentik access, and the original data. Inspect the unused
PG18 volume and preserve anything needed before explicitly removing only the
failed destination with `docker volume rm platform_authentik_db_pg18`.
Never remove `platform_authentik_db`. Retry the fixed deployment after the
destination is absent.

Once Authentik has used PostgreSQL 18, retain and recover that database.
Automatic PostgreSQL 16 restoration stops at successful conversion, before
package activation. Returning to the old volume after new writes would discard
those writes and requires a separate deliberate database recovery.

## Rebuild a host

1. Hold production workflows while restoring hosts or data.
2. From a trusted controller, follow [setup](setup.md) to review topology
   and storage, plan PVE changes, and apply only the intended rebuild. Replacement
   requires the exact VMID and can destroy the guest root disk.
3. Update `DEPLOY_SSH_KNOWN_HOSTS` with the guest keys verified through `pct`.
4. Restore the private controller bundles and GitHub environment credentials.
   Reconcile `tailnet` and `apps-host` separately.
5. With applications stopped, restore durable files and named Docker volumes from
   backup. Preserve permissions and numeric ownership when restoring PVE backups
   (for example, use `rsync -a --numeric-ids`). Confirm the shared filesystem is
   mounted correctly before writing data. For unmapped restored owners, follow
   [restore ownership](#restore-ownership) before guest reconciliation.
   Reconcile `apps-host` again to repair ownership of restored application files;
   this preserves file permissions and leaves other filesystem contents alone.
6. Dispatch `apps.yml` with `operation=deploy`. The workflow installs the apps
   bundle and activates the complete package.
7. Run the [read-only application health check](operations.md#audit-and-rollback) and check that
   restored files are available and the expected shares are writable.

## Restore ownership

Guest root can repair owners within its UID/GID mapping. Files restored on PVE
with unmapped owners, such as host UID `0`, require repair on PVE first.

With applications stopped, check the bind-source mapping in
[topology](../infra/ansible/inventory/prod/topology.json) and the application
UID/GID in [group variables](../infra/ansible/inventory/prod/group_vars/all.yml).
The current mapping starts at `100000` and the application UID/GID is `1000`,
giving PVE ownership `101000:101000`. Confirm those values and the declared
application paths before running this on the PVE console:

```sh
chown -R --no-dereference 101000:101000 \
  /var/lib/homelab/docker-apps/qbittorrent \
  /var/lib/homelab/docker-apps/copyparty \
  /var/lib/homelab/downloads \
  /var/lib/homelab/copyparty
```

This changes ownership only in the application roots and preserves file
permissions. Reconcile `apps-host` after the repair, then resume the rebuild.

## When tailnet is unavailable

Hosted jobs depend on tailnet to reach the LAN. Use a trusted LAN controller or
the PVE console to restore that connection first.

After the Headscale cutover, the apps host also owns the management control
server and identity provider. Recover or deploy that host from the LAN or PVE
console; its normal hosted jobs depend on the services being recovered. The
[management migration procedure](operations.md#move-management-access-to-headscale)
includes returning to the original hosted Tailscale connection.

The Ansible PVE reconciler protects the tailnet VMID against changes that would
break its own connection. For a required restart or replacement, use the installed
helper directly from the PVE console. Confirm `/etc/homelab/topology.json` matches
the reviewed repository revision, then inspect the plan:

```sh
/usr/local/libexec/pve-lxc-reconcile plan \
  --topology /etc/homelab/topology.json \
  --export-dir /var/backups/homelab/pct-config
```

After arranging backups, run the same command with `apply` instead of `plan`.
Add `--allow-destructive VMID` or `--allow-replacement VMID` only when the plan
requires it, using the exact VMID from topology.

After restoring the container, use setup from a trusted LAN controller to
re-establish SSH/Python access and Tailscale. Verify its SSH host key before
returning to hosted workflows.

## Storage maintenance

Repository changes declare capacity; live storage changes need a separate
maintenance operation. Use topology for LXC resources and mounts, and
[group variables](../infra/ansible/inventory/prod/group_vars/all.yml) for the
shared data LV declaration.

### Grow an LXC root disk

1. Change the host's `root_disk_gb` in
   [topology.json](../infra/ansible/inventory/prod/topology.json).
2. Run the `pve` unit in `plan` mode and review the reported changes and
   root-storage headroom.
3. Apply using the [PVE procedure](setup.md#provision-the-containers).
   Root-disk growth uses `pct resize`.
4. Run `pve` in `audit` mode and confirm the guest has the expected capacity.

Root-disk shrinking is treated as replacement. Back up the guest, review its
exported configuration, and supply `pve_lxc_reconcile_allow_replacement_vmid`
only for an intentional rebuild.

Check [space before image pulls](operations.md#release-files-and-capacity).
Prefer a reviewed root-disk grow when capacity is insufficient.

### Shrink the shared data LV

This procedure applies to ext4 on `/dev/pve/homelab-data`, mounted at
`/var/lib/homelab`. The declared LV target is `896G`; the intermediate filesystem
size is `880G`. Confirm these values against the declaration and actual device
before running commands.

Before the maintenance window:

- Verify an independent backup of `/var/lib/homelab`.
- Confirm used space fits comfortably within `880G`.
- Identify affected LXCs from the mounts in topology.
- Arrange PVE console access and hold deployment and reconciliation jobs.

Run on PVE as root, checking each result before proceeding:

1. Stop the affected LXCs and unmount `/var/lib/homelab`.
2. Check the filesystem, then shrink it before reducing the LV:

   ```sh
   e2fsck -f /dev/pve/homelab-data
   resize2fs /dev/pve/homelab-data 880G
   lvreduce -L 896G /dev/pve/homelab-data
   e2fsck -f /dev/pve/homelab-data
   ```

   Stop if a check reports unresolved errors. Never reduce the LV before the
   filesystem resize completes, or below the filesystem's size.

3. Mount `/var/lib/homelab` and verify its source and capacity.
4. Start the affected LXCs.

If resizing or filesystem checks fail, keep applications stopped until the
filesystem is repaired or [restored from backup](#rebuild-a-host).

### Verify before resuming jobs

1. Run `pve` in `audit` mode and confirm no managed topology drift.
2. Confirm the declared shared mount is available in `docker_apps` at
   `/srv/homelab` and is exposed only to the intended LXC.
3. Check qBittorrent and Copyparty can write their declared durable paths.
4. Run the [read-only application health check](operations.md#audit-and-rollback), then resume jobs.
