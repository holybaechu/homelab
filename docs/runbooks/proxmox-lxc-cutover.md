# Proxmox Two-LXC Cutover Checklist

Use the detailed `docker-compose-migration.md` runbook for commands and
rollback. The cutover gate is:

- OpenTofu manages only VMID 111 (`tailnet`) and VMID 110 (`docker_apps`).
- VMIDs 110/111 are hostname-verified and `vzdump`-backed before replacement.
- Legacy VMIDs 113, 114, and 116 are forgotten with `destroy = false`, not
  destroyed.
- Minecraft VMID 115 is permanently destroyed with the Proxmox-host path
  `/var/lib/homelab/minecraft` and matching local `vzdump-lxc-115-*` archives;
  these assets have no rollback path. The data path is deleted only after the
  retired `game` Compose project has been stopped and removed in `docker_apps`.
- VMID 110 has `/dev/net/tun`, nesting/keyctl, and the single
  `/var/lib/homelab` bind mount.
- The three active Compose projects (`platform`, `media`, and `hermes`) are
  running and Ansible live validation passes.
- Router TCP 80/443 and DHCP DNS point to `192.168.0.3`.
- Gluetun's public IP differs from the host IP.

Keep legacy VMIDs 113, 114, and 116 stopped but intact through the soak period,
and keep their pre-cutover `vzdump` archives until rollback is no longer
required. This retention does not apply to Minecraft VMID 115,
`/var/lib/homelab/minecraft`, or `vzdump-lxc-115-*` archives.
They have no rollback path.
