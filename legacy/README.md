# Legacy Docker Stacks

This directory preserves the old Docker Compose stack definitions for audit and rollback reference.

Active deployment has moved to:

- OpenTofu: `infra/opentofu`
- Ansible: `infra/ansible`
- App configuration: `apps`
- GitHub Actions CD: `.github/workflows/cd.yml`

Only these old stacks were preserved as migration source material:

- `traefik`
- `adguard`
- `tailscale`
- `qbittorrent`
- `copyparty`
- `ddns`

The old Komodo, Navidrome, OpenWebUI, and Minecraft stacks are archived as deprecated services.
