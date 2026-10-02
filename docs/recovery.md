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
| Private component bundles and connection credentials | Host access and application authentication |
| The intended repository revision | Host declarations and application configuration |

LXC configuration exports under `/var/backups/homelab/pct-config` help review
changes, but they are not data backups. Immutable releases and rollback records
also do not restore an earlier copy of application data.

## Rebuild a host

1. Hold production workflows while restoring hosts or data.
2. From a trusted controller, follow [setup](setup.md) to review topology
   and storage, plan PVE changes, and apply only the intended rebuild. Replacement
   requires the exact VMID and can destroy the guest root disk.
3. Update `DEPLOY_SSH_KNOWN_HOSTS` with the guest keys verified through `pct`.
4. Restore the private controller bundles and GitHub environment credentials.
   Reconcile `tailnet` and `apps-host` separately.
5. With applications stopped, restore durable files and named Docker volumes from
   backup. Preserve ownership and permissions. Confirm the shared filesystem is
   mounted correctly before writing data.
6. Dispatch `apps.yml` with `operation=deploy`. The workflow installs the apps
   bundle and activates the complete package.
7. Run the [release audit](operations.md#audit-and-rollback) and check that
   restored files are available and the expected shares are writable.

## When tailnet is unavailable

Hosted jobs depend on tailnet to reach the LAN. Use a trusted LAN controller or
the PVE console to restore that connection first.

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

The app release engine requires [free space for image pulls](operations.md#release-files-and-capacity).
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
4. Run the [app audit](operations.md#audit-and-rollback), then resume jobs.
