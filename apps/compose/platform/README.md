# Platform Compose Project

Runs the shared application platform: Traefik, AdGuard Home, and Cloudflare
DDNS. Traefik is the only HTTP/HTTPS ingress. AdGuard uses host networking so
LAN DNS clients retain their source addresses; only TCP/UDP 53 and the private
admin port 3000 are enabled in its rendered configuration.

Opaque state uses named volumes (`traefik_data` and `adguard_work`). The
AdGuard configuration is a bind-mounted, Ansible-rendered file because it is
declarative and must be reviewed in Git through its template.

No public DoH, DoT, or DoQ endpoint is enabled.
