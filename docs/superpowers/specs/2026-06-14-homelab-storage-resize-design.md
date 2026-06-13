# Homelab Storage Resize Design

## Context

The `homelab-data` LV is currently declared as `1200G` and is surfaced through Copyparty as roughly `1.15 TiB` total with about `600 GiB` free. Current usage is about `578 GiB`, so reducing the data LV to `896G` still leaves about `300 GiB` of free space.

The `music` and `bjh_deepfake_contest` Copyparty shares are no longer needed. They are currently declared in Copyparty configuration, Files LXC bind mounts, and Proxmox host storage directory setup.

Current LXC root disk usage:

| LXC | Current root disk | Usage |
| --- | ---: | ---: |
| edge | 4G | 56.1% |
| dns | 4G | 6.1% |
| tailnet | 4G | 22.5% |
| downloads | 8G | 20.5% |
| files | 6G | 4.3% |

## Decisions

1. Reduce the declared `homelab-data` size from `1200G` to `896G`.
2. Increase `edge` root disk from `4G` to `6G` because it has the highest root usage.
3. Keep `dns` root disk at `4G`; the current usage is low, but shrinking below 4G has little practical value.
4. Keep `tailnet` root disk at `4G`.
5. Keep `downloads` root disk at `8G`; downloaded content lives on the shared data mount, but the larger root disk leaves room for qBittorrent, WireGuard, NAT-PMP helpers, package cache, and logs.
6. Reduce the declared `files` root disk from `6G` to `4G`.
7. Remove all active `music` and `bjh_deepfake_contest` share declarations, mount declarations, and storage directory management.

## Scope

Update repo-managed declarations only:

- `infra/ansible/inventory/prod/group_vars/all.yml`
- `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`
- `infra/ansible/roles/copyparty/tasks/main.yml`
- `infra/ansible/roles/copyparty/templates/copyparty.conf.j2`
- `apps/files/copyparty.conf`
- `infra/opentofu/envs/prod/terraform.tfvars`
- `infra/opentofu/envs/prod/terraform.tfvars.example`
- tests that assert Copyparty and storage behavior

## Runtime Migration

Repo changes must not automatically shrink live storage. Shrinking existing ext4/LVM volumes is operationally risky and should be handled as a separate Proxmox maintenance procedure.

The live `homelab-data` shrink should be documented as a manual runbook step:

1. Back up important data from `/var/lib/homelab`.
2. Stop affected LXCs.
3. Unmount the data filesystem from Proxmox.
4. Run filesystem checks.
5. Shrink ext4 to a size safely below the target LV size.
6. Reduce the LV to `896G`.
7. Re-run filesystem checks.
8. Mount the filesystem and start LXCs.
9. Verify Copyparty and qBittorrent paths.

Existing LXC root disk shrink should also be handled manually or through rebuild/restore. The repo declaration should describe the intended state, not perform a destructive shrink.

## Validation

Automated validation should confirm:

- Copyparty no longer exposes `/music` or `/bjh_deepfake_contest`.
- Files LXC root options no longer mount `/srv/music` or `/srv/bjh_deepfake_contest`.
- Proxmox storage setup no longer creates or chmods removed share directories.
- Declared sizes are `homelab_data_lv_size: 896G`, `edge.root_disk_gb = 6`, and `files.root_disk_gb = 4`.

Manual validation after runtime maintenance:

- `copyparty.hchu.me` shows the reduced total capacity.
- `/downloads/complete` remains visible read-only through the Files LXC at `/srv/downloads`.
- `/downloads/incomplete` is not exposed through the Files LXC.
- qBittorrent can still write completed and incomplete downloads.
