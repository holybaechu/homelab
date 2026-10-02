# Homelab Compose package

This directory contains the services and configuration deployed to `docker_apps`
as the `homelab` Compose project.

| File | Use |
| --- | --- |
| [compose.yml](compose.yml) | Services, image pins, routes, networks, and data mounts |
| [config/](config/) and [traefik.yml](traefik.yml) | Application configuration |
| [prepare_release.py](prepare_release.py) | Validate credentials and generate private configuration |
| [smoke.sh](smoke.sh) | Check DNS, ingress, AdGuard policy, and qBittorrent behavior |

## Making changes

1. Edit the service or its configuration here. Keep images and the VueTorrent
   modification pinned by readable tag and exact digest.
2. Keep service checks with the package. The release engine checks the running
   service set and health; `smoke.sh` checks application behavior and reads
   ingress endpoints from Compose.
3. Run [local validation](../../../README.md#validate-locally) from the repository
   root.
4. Deploy using the [operations guide](../../../docs/operations.md).

Cloudflare DDNS uses a scratch image without a healthcheck command. Its
`homelab.health=process` label requires one running container, no restart in
progress, and zero activation restarts.

## Credentials and local preparation

The production bundle is `/etc/homelab/secrets/apps.json`. Its schema and
password-hash instructions are in the [secrets guide](../../../secrets/README.md).

To check a private bundle locally, run this from the repository root on a trusted
Linux controller. It prepares a temporary copy without starting services:

```sh
staged="$(mktemp -d)"
cp -R apps/compose/homelab "$staged/apps"
python3 "$staged/apps/prepare_release.py" \
  --secret-bundle /absolute/private/path/apps.json \
  --release-root "$staged/apps" \
  --topology infra/ansible/inventory/prod/topology.json
docker compose --project-name homelab --project-directory "$staged/apps" \
  -f "$staged/apps/compose.yml" config --quiet
```

Remove the temporary directory after checking it; it contains private
`.secrets/` and `generated/` files. The production workflow performs preparation
with the topology snapshot from the selected release commit.

## Configuration and data

Change managed settings in this package. Copyparty configuration is mounted
read-only, AdGuard configuration is regenerated during release preparation, and
qBittorrent configuration is reapplied on restart. UI preference edits may be
overwritten.

Data mounts and named volumes are listed in `compose.yml`. Back them up using
the [recovery guide](../../../docs/recovery.md); rollback restores
application code and configuration, not an earlier copy of the data.
