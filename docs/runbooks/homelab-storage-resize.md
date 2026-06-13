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

- `edge` root disk grows from `4G` to `6G`; this can be applied through the
  normal OpenTofu/Proxmox workflow.
- `files` root disk is declared as `4G`; shrinking an existing root disk should
  be handled through rebuild/restore or a cautious manual shrink, not by routine
  automation.

## Verification

- Copyparty reports the reduced total capacity.
- `/srv/downloads` is mounted read-only in the `files` LXC.
- `/downloads` is writable in the `downloads` LXC.
- `/downloads/incomplete` is not mounted into the `files` LXC.
- `/srv/music` and `/srv/bjh_deepfake_contest` are no longer active Copyparty
  shares.
- qBittorrent can still write complete and incomplete downloads.
