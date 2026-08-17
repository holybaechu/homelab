# Proxmox LXC Cutover Checklist

Use the detailed `docker-compose-migration.md` runbook for commands and
rollback. The cutover gate is:

- OpenTofu manages VMID 111 (`tailnet`), VMID 110 (`docker_apps`), and the
  dedicated unprivileged native OpenClaw VMID 118 (`openclaw`).
- VMIDs 110/111 are hostname-verified and `vzdump`-backed before replacement.
- Legacy VMIDs 113, 114, and 116 are forgotten with `destroy = false`, not
  destroyed.
- Minecraft VMID 115 is permanently destroyed with the Proxmox-host path
  `/var/lib/homelab/minecraft` and matching local `vzdump-lxc-115-*` archives;
  these assets have no rollback path. The data path is deleted only after the
  retired `game` Compose project has been stopped and Docker reports no
  `com.docker.compose.project=game` containers. Proxmox also verifies the
  `/dev/pve/homelab-data` mount identity and rejects target/descendant mounts.
- VMID 111 retains `/dev/net/tun` for Tailscale.
- VMID 110 has no TUN passthrough; it retains nesting/keyctl and the single
  `/var/lib/homelab` bind mount.
- VMID 118 has nesting/keyctl for its local CTF Docker Engine and no TUN
  passthrough. Its narrow host bind exposes the CTF workspace at
  `/var/lib/openclaw/workspaces/ctf`; generated sandbox skills remain inside
  the LXC. Port 18789 ingress is accepted only from Traefik on `192.168.0.3`.
- No unrelated homelab data or Docker socket is mounted into a CTF sandbox.
- The active Compose projects (`platform`, `media`, `code`, and `openclaw`) are
  running and Ansible live validation passes.
- The media project has one direct qBittorrent instance at
  `https://qbt.home.hchu.me`; TCP and UDP peer port `35435` remain published.
- The retired VPN qBittorrent data at
  `/srv/homelab/docker-apps/qbittorrent-vpn` remains preserved but unmanaged
  for recovery.
- Hermes Agent is retired. Its former `/srv/homelab/hermes` data remains
  preserved but unmanaged and VMID 116 must remain stopped.
- Router TCP 80/443 and DHCP DNS point to `192.168.0.3`.
- Router WAN TCP and UDP `35435` forward to `192.168.0.3:35435`.

Keep legacy VMIDs 113, 114, and 116 stopped but intact through the soak period,
and keep their pre-cutover `vzdump` archives until rollback is no longer
required. This retention does not apply to Minecraft VMID 115,
`/var/lib/homelab/minecraft`, or `vzdump-lxc-115-*` archives.
They have no rollback path.
