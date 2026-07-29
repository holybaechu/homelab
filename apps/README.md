# Applications

All application services run through Docker Compose on the `docker_apps` LXC.

- `compose/platform`: Traefik, AdGuard Home, Cloudflare DDNS.
- `compose/media`: Gluetun, qBittorrent, Copyparty.
- `compose/hermes`: Hermes Agent Discord gateway.

Ansible copies these projects to `/opt/homelab-compose`, renders private `.env`
and configuration files, and reconciles them automatically in CI/CD.
