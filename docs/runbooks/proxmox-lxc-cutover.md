# Proxmox LXC Cutover Runbook

## Pre-Cutover

1. Confirm OpenTofu plan is clean.
2. Confirm Ansible site playbook completes with no failures.
3. Confirm validation playbook completes with no failures.
4. Confirm any required service data backups already exist outside this repo.
5. Snapshot the five new LXCs in Proxmox.

## DNS Cutover

1. Set router DHCP DNS server to `192.168.0.3`.
2. Confirm `dig @192.168.0.3 hchu.me` works from a LAN client.
3. Confirm `adguard.home.hchu.me` resolves to `192.168.0.4`.

## Edge Cutover

1. Forward TCP `80` and `443` on the router to `192.168.0.4`.
2. Open `https://copyparty.hchu.me`.
3. Open `https://qbt.home.hchu.me` from LAN or Tailscale and confirm non-private clients receive HTTP `403`.

## Encrypted DNS Cutover

1. Do not publish a public DoH endpoint.
2. Keep AdGuard encrypted DNS ports private to LAN/Tailscale unless public DoT/DoQ is intentionally re-enabled later.
3. Confirm AdGuard has a valid `adguard.home.hchu.me` certificate for private HTTPS upstreams.

## Downloads Cutover

1. Confirm `/downloads/incomplete` is not mounted into the `files` LXC.
2. Confirm `/downloads/complete` is mounted read-only in the `files` LXC at `/srv/downloads`.
3. Confirm qBittorrent external IP is a Proton VPN IP.
4. Confirm NAT-PMP updater sets a non-zero qBittorrent listen port.

## Rollback

1. Restore router port forwards to the previous edge service.
2. Restore router DHCP DNS to the previous DNS server.
3. Stop affected new LXCs.
4. Restore Proxmox snapshots if service state was changed.
5. Re-run validation after restoring the previous Git SHA.
