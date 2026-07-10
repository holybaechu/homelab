# Proxmox Two-LXC Cutover Checklist

Use the detailed `docker-compose-migration.md` runbook for commands and
rollback. The cutover gate is:

- OpenTofu manages only VMID 111 (`tailnet`) and VMID 110 (`docker_apps`).
- VMIDs 110/111 are hostname-verified and `vzdump`-backed before replacement.
- Legacy VMIDs 113-116 are forgotten with `destroy = false`, not destroyed.
- VMID 110 has `/dev/net/tun`, nesting/keyctl, and the single
  `/var/lib/homelab` bind mount.
- Every Compose project is running and Ansible live validation passes.
- Router TCP 80/443 and DHCP DNS point to `192.168.0.3`.
- Gluetun's public IP differs from the host IP.
- The first encrypted off-host Restic snapshot and a staged test restore pass.

Keep legacy VMIDs 113-116 stopped but intact through the soak period, and keep
the pre-cutover `vzdump` archives until rollback is no longer required.
