# Two-LXC Docker Compose Migration

## Target architecture

The managed production topology contains exactly two LXCs:

- `tailnet` (`192.168.0.4`, VMID 111): Tailscale subnet router and exit node.
- `docker_apps` (`192.168.0.3`, VMID 110): every application, managed with
  Docker Compose.

The Docker host runs four projects in dependency order:

1. `platform`: Traefik, AdGuard Home, and Cloudflare DDNS.
2. `media`: Gluetun, qBittorrent, and Copyparty.
3. `game`: Paper and Velocity/Geyser.
4. `hermes`: the official Hermes Agent gateway image.

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
AdGuard work data, and Gluetun state.

## Automatic CI/CD

Every push to `main` affecting `apps/**`, `infra/**`, or deployment scripts
triggers `.github/workflows/cd.yml`. CD connects through Tailscale, plans and
applies OpenTofu, bootstraps both LXCs, renders secret Compose environments and
application configs with Ansible, pulls/builds images, runs `docker compose up
-d --build --remove-orphans`, and performs live validation.

The deployment also retires the former `backup` project: it runs `docker
compose down --volumes --remove-orphans` when the deployed project still
exists, then removes `/opt/homelab-compose/backup`. This removes only the local
container, cache/state volumes, and deployed files; it does not delete data in
the former off-host repository.

The first consolidated apply intentionally renumbers the retained Docker and
tailnet LXCs into the two lowest legacy service slots. Before OpenTofu runs,
Ansible hostname-verifies and backs up source VMIDs 117/112 and the legacy
occupants at 110/111, then destroys only the verified `edge` and `dns`
occupants. The plan guard permits only the exact 117 to 110 and 112 to 111
replacements. Every other destructive plan remains blocked.

The four higher legacy application LXCs remain forgotten with
`destroy = false` and are stopped during bootstrap.

Required GitHub `prod` secret for consolidated routing:

- `CLOUDFLARE_TRAEFIK_TOKEN`

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
4. Verify `copyparty.hchu.me`, private qBittorrent/AdGuard routes, Minecraft
   Java/Bedrock connectivity, Tailscale routing, and Hermes Discord delivery.
5. Confirm the qBittorrent public address differs from the Docker host address
   and the forwarded port appears in Gluetun logs.

## Data protection

Application-level backups are not managed by this repository. The cutover
workflow's local `vzdump` archives are rollback artifacts, not recurring data
backups.

## Rollback

1. Stop the affected Compose projects on VMID 110.
2. Restore the timestamped `vzdump` backups of the former 110/111 occupants to
   unused temporary VMIDs.
3. Restore router port forwards and DHCP DNS to those recovery LXCs.
4. Start the retained 113-116 legacy LXCs and validate them before accepting traffic.

After the soak period and separate data-protection verification, destroy the
unmanaged 113-116 legacy LXCs manually. They are intentionally no longer part
of OpenTofu state.
