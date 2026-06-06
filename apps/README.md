# Apps

This directory contains service configuration that is deployed into Proxmox LXCs by Ansible.

- `edge`: Caddy and Cloudflare DDNS.
- `dns`: AdGuard Home ACME support.
- `downloads`: qBittorrent and Proton VPN helper scripts.
- `files`: Copyparty configuration.

Runtime secrets are supplied through SOPS or GitHub Actions secrets. They are not stored in this directory.
