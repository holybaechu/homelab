# DNS Compose project

Runs AdGuard Home on the Docker apps host while the legacy `dns` LXC remains available for rollback during migration.

## Ports

- `53/tcp` and `53/udp` for plain DNS
- `853/tcp` and `853/udp` for DNS-over-TLS / DNS-over-QUIC
- Web UI is not published directly; Traefik proxies `adguard.home.hchu.me` to the container's internal port `3000` with the `private-only` middleware.

## Managed state

Ansible renders `/srv/docker-apps/adguard/conf/AdGuardHome.yaml` from the same Cloudflare/upstream policy used by the LXC role. Runtime work data lives under `/srv/docker-apps/adguard/work`.

AdGuard TLS files for DoT/DoQ live under `/srv/docker-apps/adguard/tls` and are renewed by a host-side systemd timer using the existing Cloudflare DNS-01 token.

## Policy

Do not add a public `dns.hchu.me` DoH route. AdGuard should keep `adguard.home.hchu.me` as its TLS/certificate server name and Cloudflare DNS (`tls://1.1.1.1`, `tls://1.0.0.1`) as upstreams.
