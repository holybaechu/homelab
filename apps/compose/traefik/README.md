# Traefik Compose Project

Traefik is the Docker-first replacement candidate for the current Caddy edge.
Keep the existing `edge`/Caddy LXC as rollback until this stack has issued
certificates and route parity checks pass.

Route policy intentionally preserves the current Caddy behavior:

- no public `dns.hchu.me` or `/dns-query` route,
- private-only middleware for `*.home.hchu.me` routes,
- `adguard.home.hchu.me` as the AdGuard upstream TLS server name,
- PVE uses SAMEORIGIN frame headers,
- router route omits `nosniff` for the current UI asset behavior.
