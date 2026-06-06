# Proxmox LXC Cutover Runbook

## Pre-Cutover

1. Confirm OpenTofu plan is clean.
2. Confirm Ansible site playbook completes with no failures.
3. Confirm validation playbook completes with no failures.
4. Back up old Docker service data:
   - `scripts/migration/backup-adguard.sh`
   - `scripts/migration/backup-qbittorrent.sh`
   - `scripts/migration/backup-copyparty.sh`
5. Snapshot the five new LXCs in Proxmox.
6. Snapshot or back up the old Docker host.

## DNS Cutover

1. Set router DHCP DNS server to `192.168.0.11`.
2. Confirm `dig @192.168.0.11 hchu.me` works from a LAN client.
3. Confirm `adguard.home.hchu.me` resolves to `192.168.0.10`.

## Edge Cutover

1. Forward TCP `80` and `443` on the router to `192.168.0.10`.
2. Open `https://copyparty.hchu.me`.
3. Open `https://qbt.home.hchu.me` from LAN or Tailscale and confirm non-private clients receive HTTP `403`.

## Encrypted DNS Cutover

1. Forward TCP `853` to `192.168.0.11` only if public DoT is required.
2. Forward UDP `853` to `192.168.0.11` only if public DoQ is required.
3. Confirm AdGuard has a valid `dns.hchu.me` certificate.

## Downloads Cutover

1. Confirm `/downloads/incomplete` is not mounted into the `files` LXC.
2. Confirm `/downloads/complete` is mounted read-only in the `files` LXC at `/srv/downloads`.
3. Confirm qBittorrent external IP is a Proton VPN IP.
4. Confirm NAT-PMP updater sets a non-zero qBittorrent listen port.

## Rollback

1. Restore router port forwards to the old Docker edge.
2. Restore router DHCP DNS to the previous DNS server.
3. Stop affected new LXCs.
4. Restore Proxmox snapshots if service state was changed.
5. Re-run validation after restoring the previous Git SHA.
