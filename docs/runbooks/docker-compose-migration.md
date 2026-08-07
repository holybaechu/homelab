# Two-LXC Docker Compose Migration

## Target architecture

The managed production topology contains exactly two LXCs:

- `tailnet` (`192.168.0.4`, VMID 111): Tailscale subnet router and exit node.
- `docker_apps` (`192.168.0.3`, VMID 110): every application, managed with
  Docker Compose.

The Docker host runs two workload projects in dependency order:

1. `platform`: Traefik, AdGuard Home, and Cloudflare DDNS.
2. `media`: one direct qBittorrent instance, Copyparty, and MeTube. The
   qBittorrent Web UI is private at `https://qbt.home.hchu.me`, while peer port
   `35435` is published over TCP and UDP for direct inbound connectivity.

VMID 111 retains `/dev/net/tun` for Tailscale. VMID 110 has no TUN
passthrough because the application stack no longer contains Gluetun or any
other VPN-routed service.

Ansible also owns the separate `arcane-control` management project at
`/opt/homelab-control/arcane`. Arcane manages the workload tree at
`/opt/homelab-compose` but never manages itself.

## Storage policy

Use bind mounts when data is shared, user-owned, independently backed up, or
must migrate from an old service:

- `/srv/homelab/downloads`
- `/srv/homelab/copyparty`
- `/srv/homelab/docker-apps/qbittorrent`
- `/srv/homelab/docker-apps/copyparty`
- `/srv/homelab/docker-apps/arcane/data`

Use named volumes for opaque state owned by one application: Traefik ACME and
AdGuard work data.

The retired VPN qBittorrent state at
`/srv/homelab/docker-apps/qbittorrent-vpn` is intentionally preserved for
manual recovery, but neither Ansible nor Arcane manages or mounts it.

Hermes Agent is retired. `/srv/homelab/hermes` is intentionally preserved for
manual recovery or later deletion, but neither Ansible nor Arcane manages it.

## Automatic CI/CD

Every push to `main` affecting `apps/**`, `infra/**`, or deployment scripts
triggers `.github/workflows/cd.yml`. Changes confined to the known workload
Compose directories deploy only the affected projects through Arcane and then
run live validation. Mixed, Arcane control-plane, infrastructure, and deployment
script changes use the full path: CD connects through Tailscale, plans and
applies OpenTofu, bootstraps both LXCs, renders secret Compose environments and
application configs with Ansible, reconciles the projects, and performs live
validation.

The deployment also retires the former `backup` and `hermes` projects: it runs
`docker compose down --volumes --remove-orphans` when a deployed project still
exists, then removes its directory under `/opt/homelab-compose`. The Arcane
reconciliation also removes the retired Hermes GitOps sync. Hermes persistent
data at `/srv/homelab/hermes` remains untouched and unmanaged.

The first consolidated apply intentionally renumbers the retained Docker and
tailnet LXCs into the two lowest legacy service slots. Before OpenTofu runs,
Ansible hostname-verifies and backs up source VMIDs 117/112 and the legacy
occupants at 110/111, then destroys only the verified `edge` and `dns`
occupants. The plan guard permits only the exact 117 to 110 and 112 to 111
replacements. Every other destructive plan remains blocked.

The retained legacy application VMIDs 113, 114, and 116 remain forgotten with
`destroy = false` and are stopped during bootstrap. Minecraft VMID 115 is the
exception: deployment permanently destroys it and its matching local archives.

Required GitHub `prod` secret for consolidated routing:

- `CLOUDFLARE_TRAEFIK_TOKEN`

Arcane also requires stable `ARCANE_ENCRYPTION_KEY` and `ARCANE_JWT_SECRET`
values in the `prod` environment. Their exact format and recovery requirements
are documented in `docs/runbooks/arcane.md`.

Retain the other service secrets documented in `secrets/README.md`.

For the one-time renumber only, set the GitHub `prod` environment variable
`LOW_ID_CUTOVER_CONFIRMED=true`. The preflight stops the legacy application
LXCs, hostname-verifies the affected containers, and creates local `vzdump`
archives before replacement. The variable may be removed after VMIDs 110/111
have the target hostnames.

## Pre-cutover

1. Set the one-time GitHub environment confirmation described above.
2. Run CI and inspect the OpenTofu plan. It may replace only
   `docker_apps` 117 to 110 and `tailnet` 112 to 111.
3. The automated preflight hostname-verifies the affected containers and creates
   local `vzdump` archives before replacement.
4. Deploy VMID 110 and wait for the Ansible validation playbook to pass.

## Network cutover

1. Change router TCP 80/443 forwards from the old edge IP to `192.168.0.3`.
2. Keep router DHCP DNS at `192.168.0.3`.
3. Renew a LAN DHCP lease and verify `dig @192.168.0.3 example.com`.
4. Verify `copyparty.hchu.me`, `qbt.home.hchu.me`, the private AdGuard and
   Arcane routes, and Tailscale routing.
5. Confirm qBittorrent uses the Docker host's direct public address and that
   TCP and UDP port `35435` are published. Keep the router forwarding WAN TCP
   and UDP `35435` to `192.168.0.3:35435`.

## Data protection

Application-level backups are not managed by this repository. The cutover
workflow's local `vzdump` archives are rollback artifacts, not recurring data
backups.

Arcane state and the stable `ARCANE_ENCRYPTION_KEY`/`ARCANE_JWT_SECRET` values
that protect it must be preserved as one recovery unit. The database persists
on the shared data mount; Ansible re-renders runtime-group-only secret files from
the GitHub `prod` environment after an LXC root-filesystem replacement. This is
not an off-host backup.

The preserved `/srv/homelab/docker-apps/qbittorrent-vpn` tree is an unmanaged
recovery artifact, not an active service or a backup. Protect or remove it
manually according to the desired retention policy.

## Rollback

1. Stop the affected Compose projects on VMID 110.
2. Restore the timestamped `vzdump` backups of the former 110/111 occupants to
   unused temporary VMIDs.
3. Restore router port forwards and DHCP DNS to those recovery LXCs.
4. Start only the retained non-Hermes legacy VMIDs 113 and 114 and validate
   them before accepting traffic. Do not restart the retired Hermes VMID 116.

Minecraft has no rollback path: VMID 115, the Proxmox-host path
`/var/lib/homelab/minecraft`, and local `vzdump-lxc-115-*` archives are
permanently deleted during deployment. The host data deletion runs only after
the retired `game` Compose project has been stopped and removed in
`docker_apps` and Docker reports no containers labeled
`com.docker.compose.project=game`. Before deletion, Proxmox verifies that
`/var/lib/homelab` is the root of `/dev/pve/homelab-data` and that no mountpoint
exists at or below the Minecraft target.

After the soak period and separate data-protection verification, destroy the
unmanaged VMIDs 113, 114, and 116 manually. They are intentionally no longer
part of OpenTofu state.
