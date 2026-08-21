# Homelab Storage Resize Runbook

## Intent

This runbook applies the manual runtime side of the repo-declared storage resize.
Repo changes declare the desired future shape; they do not safely shrink existing
ext4 or LVM volumes.

## Safety

- Back up important data from `/var/lib/homelab` before shrinking.
- Stop affected LXCs before unmounting or resizing shared storage.
- Confirm current used space is comfortably below the target size.
- Do not shrink ext4 or LVM below observed used space.
- Prefer a maintenance window; failed shrink operations can make the filesystem
  unavailable until repaired or restored.

## Data LV Shrink Outline

The declared target LV size is `896G`. The intermediate ext4 size below leaves
space between the filesystem and final LV size before the LV is reduced.

1. Stop affected LXCs.
2. Confirm `/var/lib/homelab` has a current backup.
3. Unmount `/var/lib/homelab`.
4. Run `e2fsck -f /dev/pve/homelab-data`.
5. Run `resize2fs /dev/pve/homelab-data 880G`.
6. Run `lvreduce -L 896G /dev/pve/homelab-data`.
7. Run `e2fsck -f /dev/pve/homelab-data`.
8. Mount `/var/lib/homelab`.
9. Start affected LXCs.

## LXC Root Disk Changes

- Change `root_disk_gb` for one of the three hosts in
  `infra/ansible/inventory/prod/topology.json`.
- Preview the exact live diff with the `pve` unit and
  `pve_lxc_reconcile_mode=plan`. A root-disk grow is a routine safe-field
  reconcile through `pct resize`.
- A shrink is classified as replacement and fails closed. Back up the guest,
  review the exported `pct config`, and use the exact
  `pve_lxc_reconcile_allow_replacement_vmid` only for an intentional rebuild.

## Verification

- The `pve` audit reports no topology drift for VMIDs 110, 111, and 118.
- `/var/lib/homelab` is mounted on the Proxmox host and exposed only to the
  application LXC at `/srv/homelab`.
- `/var/lib/homelab/openclaw-ctf` is exposed only to the OpenClaw LXC at
  `/var/lib/openclaw/workspaces/ctf` with the declared unprivileged UID map.
- qBittorrent and Copyparty can still write their declared durable paths.
