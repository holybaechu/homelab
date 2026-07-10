# Minecraft Compose Runbook

Minecraft runs in the `game` Compose project on `docker_apps`
(`192.168.0.3`). Paper is reachable only on the project network; Velocity
publishes TCP 25565 and Geyser publishes UDP 19132.

Persistent data:

- `/srv/homelab/minecraft/paper`
- `/srv/homelab/minecraft/velocity`

Deploy or reconcile:

```bash
ssh root@192.168.0.3 \
  'cd /opt/homelab-compose/game && docker compose up -d --pull always'
```

Inspect:

```bash
ssh root@192.168.0.3 \
  'cd /opt/homelab-compose/game && docker compose ps && docker compose logs --tail=100'
```

Router forwards, when public access is desired:

- TCP 25565 to `192.168.0.3:25565`
- UDP 19132 to `192.168.0.3:19132`

Stop the project before restoring world or plugin data. Paper and Velocity use
the same forwarding secret already stored in their migrated data directories.
