# Two-LXC Docker Compose Migration

## Target architecture

The managed production topology contains exactly two LXCs:

- `tailnet` (`192.168.0.4`, VMID 111): Tailscale subnet router and exit node.
- `docker_apps` (`192.168.0.3`, VMID 110): every application, managed with
  Docker Compose.

The Docker host runs five projects in dependency order:

1. `platform`: Traefik, AdGuard Home, and Cloudflare DDNS.
2. `media`: Gluetun, qBittorrent, and Copyparty.
3. `game`: Paper and Velocity/Geyser.
4. `hermes`: the official Hermes Agent gateway image.
5. `backup`: encrypted Restic backups of qBittorrent and Copyparty data.

## Storage policy

Use bind mounts when data is shared, user-owned, independently backed up, or
must migrate from an old service:

- `/srv/homelab/downloads`
- `/srv/homelab/copyparty`
- `/srv/homelab/docker-apps/qbittorrent`
- `/srv/homelab/docker-apps/copyparty`
- `/srv/homelab/minecraft`
- `/srv/homelab/hermes`

Use named volumes for opaque state owned by one application: Traefik ACME,
AdGuard work data, Gluetun state, Restic cache, and backup health state.

## Automatic CI/CD

Every push to `main` affecting `apps/**`, `infra/**`, or deployment scripts
triggers `.github/workflows/cd.yml`. CD connects through Tailscale, plans and
applies OpenTofu, bootstraps both LXCs, renders secret Compose environments and
application configs with Ansible, pulls/builds images, runs `docker compose up
-d --build --remove-orphans`, and performs live validation.

The first consolidated apply intentionally renumbers the retained Docker and
tailnet LXCs into the two lowest legacy service slots. Before OpenTofu runs,
Ansible hostname-verifies and backs up source VMIDs 117/112 and the legacy
occupants at 110/111, then destroys only the verified `edge` and `dns`
occupants. The plan guard permits only the exact 117 to 110 and 112 to 111
replacements. Every other destructive plan remains blocked.

The four higher legacy application LXCs remain forgotten with
`destroy = false` and are stopped during bootstrap.

Required new GitHub `prod` secrets:

- `CLOUDFLARE_TRAEFIK_TOKEN`
- `BACKUP_RESTIC_REPOSITORY`
- `BACKUP_RESTIC_PASSWORD`
- `BACKUP_AWS_ACCESS_KEY_ID`
- `BACKUP_AWS_SECRET_ACCESS_KEY`

Retain the other service secrets documented in `secrets/README.md`.

For the one-time renumber only, set the GitHub `prod` environment variable
`LOW_ID_CUTOVER_CONFIRMED=true`. The preflight stops the legacy application
LXCs, migrates qBittorrent and Copyparty data, creates and checks an encrypted
off-host Restic snapshot, and only then authorizes replacement. The variable
may be removed after VMIDs 110/111 have the target hostnames.

## Pre-cutover

1. Confirm the Restic repository is off-host and credentials are bucket-scoped.
2. Set the one-time GitHub environment confirmation described above.
3. Run CI and inspect the OpenTofu plan. It may replace only
   `docker_apps` 117 to 110 and `tailnet` 112 to 111.
4. The automated preflight stops the legacy write-heavy services, performs the
   final qBittorrent/Copyparty sync, and verifies the first Restic snapshot.
5. Deploy VMID 110 and wait for the Ansible validation playbook to pass.
6. Confirm another scheduled Restic snapshot completes before retiring legacy data.

## Network cutover

1. Change router TCP 80/443 forwards from the old edge IP to `192.168.0.3`.
2. Keep router DHCP DNS at `192.168.0.3`.
3. Renew a LAN DHCP lease and verify `dig @192.168.0.3 example.com`.
4. Verify `copyparty.hchu.me`, private qBittorrent/AdGuard routes, Minecraft
   Java/Bedrock connectivity, Tailscale routing, and Hermes Discord delivery.
5. Confirm the qBittorrent public address differs from the Docker host address
   and the forwarded port appears in Gluetun logs.

## Backup and restore

The backup container starts a Restic backup immediately, then every 24 hours.
It includes qBittorrent configuration and downloads, Copyparty share data, and
Copyparty runtime/index state. Retention is 7 daily, 5 weekly, and 12 monthly.

List snapshots:

```bash
ssh root@192.168.0.3 \
  'cd /opt/homelab-compose/backup && docker compose exec restic-backup restic snapshots'
```

Restore into a staging directory, inspect it, stop the affected Compose project,
and only then copy selected data back. Never restore over running containers.

## Rollback

1. Stop the affected Compose projects on VMID 110.
2. Restore the timestamped `vzdump` backups of the former 110/111 occupants to
   unused temporary VMIDs.
3. Restore router port forwards and DHCP DNS to those recovery LXCs.
4. Start the retained 113-116 legacy LXCs and validate them before accepting traffic.
5. Restore data from the pre-cutover Restic snapshot if new services modified it
   incompatibly.

After the soak period and a tested Restic restore, destroy the unmanaged
113-116 legacy LXCs manually. They are intentionally no longer part of
OpenTofu state.
