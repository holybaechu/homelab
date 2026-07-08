# Docker Compose Migration Runbook

This migration moves the app plane from one LXC per service to a Docker Compose
host while keeping network and control planes independent.

## Target split

- `docker_apps` LXC: Traefik, gluetun, qBittorrent, Copyparty, and Minecraft.
- `tailnet` LXC: Tailscale subnet router / exit node.
- `hermes` LXC: Hermes gateway, Kanban dispatcher, skills/memory/profile/cron state, and recovery tooling.
- `dns` LXC: AdGuard remains separate during the first migration phase.

## Why Tailnet stays outside Docker

Tailnet advertises `192.168.0.0/24`, may act as an exit node, accepts routes,
and requires forwarding plus `/dev/net/tun`. Putting that inside Docker would
usually require `NET_ADMIN`, host networking or extra routing glue, persistent
Tailscale state, and careful iptables/nftables behavior. It is a network
appliance and should remain boring and recoverable.

## Why Hermes stays outside Docker

Hermes is the control plane used to inspect, patch, and recover the Docker
stack. It owns live mutable state under `/var/lib/hermes`, profile/skill/memory
state, cron, Kanban dispatch, Browserbase/local browser settings, and gateway
secrets. If Docker or Compose breaks, Hermes should still be reachable.

Do not mount `/var/run/docker.sock` into Hermes unless a separate design review
explicitly accepts that root-equivalent trust boundary.

## Cutover guardrails

1. Deploy `docker_apps` without removing old LXCs.
2. Keep Caddy/edge running as rollback until Traefik route parity is proven.
3. Keep old qBittorrent, Copyparty, and Minecraft LXCs stopped but recoverable
   during soak.
4. Do not expose public `dns.hchu.me` DoH.
5. Keep qBittorrent `/public` read-write so seeded public content remains
   available through Copyparty.
6. Keep Copyparty plaintext `password` entries in `COPYPARTY_USERS_JSON` unless
   password hashing is explicitly requested.

## Validation

Before deployment:

```bash
./scripts/ci/validate-compose.sh
cd infra/opentofu/envs/prod
opentofu fmt -check
opentofu validate
```

After deployment but before cutover:

```bash
ssh root@192.168.0.10 'docker ps'
ssh root@192.168.0.10 'cd /opt/homelab-compose/traefik && docker compose ps'
ssh root@192.168.0.10 'cd /opt/homelab-compose/media && docker compose ps'
ssh root@192.168.0.10 'cd /opt/homelab-compose/game && docker compose ps'
```

## Rollback

- Edge rollback: restore router/DNS forwarding to `192.168.0.4` and keep Traefik
  stopped.
- Media rollback: stop the media Compose project and restart old `downloads` and
  `files` services.
- Minecraft rollback: stop the game Compose project and restart the old
  Minecraft LXC services.
- Do not stop Tailnet or Hermes as part of app rollback.
