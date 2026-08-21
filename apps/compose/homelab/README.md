# Homelab application release package

This directory is the complete deployable unit for the application host:

- `compose.yml` defines all services and owns `homelab_proxy`;
- `config/` and `traefik.yml` contain nonsecret runtime policy;
- `release.json` is the fixed package/deployer contract;
- `prepare_release.py` validates one component secret bundle and writes the
  private generated files inside a staged copy of this directory; and
- `smoke.sh` derives ingress endpoints from Compose, then verifies DNS,
  ingress, required AdGuard policy, and qBittorrent behavior.

Every service image and the VueTorrent modification are tag-and-digest pinned.
Tags remain readable update coordinates, while the OCI digests make activation,
audit, and rollback select the same bytes even if an upstream tag moves.

An application change therefore normally touches only this package. It does
not require an Ansible template, inventory variable, runtime `.env`, service
manifest, or separately maintained validation list.

## Secret bundle

The deployer supplies the root-owned, mode `0600`, regular file
`/etc/homelab/secrets/apps.json`. Version 1 has this exact shape:

```json
{
  "component": "apps",
  "version": 1,
  "cloudflare": {
    "traefik_dns_api_token": "...",
    "ddns_api_token": "..."
  },
  "adguard": {
    "username": "admin",
    "password_hash": "$2y$..."
  },
  "qbittorrent": {
    "username": "...",
    "password_hash": "@ByteArray(base64-salt:base64-pbkdf2-digest)"
  },
  "copyparty_users": [
    {"name": "...", "password": "..."}
  ]
}
```

The first `copyparty_users` entry owns the writable shares; every listed user
can read the shared read-only area.

Unknown keys, wrong component/version values, unsafe account names, multiline
values, malformed hashes, symlinks, and overly broad POSIX permissions fail
closed. The materializer creates only `.secrets/` and `generated/`; both are
ignored by Git and every output is mode `0600` on POSIX hosts.

Prepare an isolated staged copy with:

```sh
python3 prepare_release.py \
  --secret-bundle /etc/homelab/secrets/apps.json \
  --release-root "$PWD" \
  --topology ../../../infra/ansible/inventory/prod/topology.json
docker compose --project-name homelab -f compose.yml config --quiet
```

The production release engine performs this before `pull` or `up`. It owns the
generic Compose render, health, and exact-running-set gates once, then executes
package-local `smoke.sh` only for application semantics. Any failure restores
the previously active immutable release. The scratch DDNS image declares the
package-local `homelab.health=process` exception; the engine derives it from
the rendered model and requires exactly one running, non-restarting container
with zero activation restarts.

## Host primitives and recovery

The host must provide `/srv/homelab`, the declared persistent directories, the
local root CA mounted by Traefik, Docker, and the component secret bundle. The
bundle builder copies the exact repository topology into the immutable package;
the materializer renders cross-host routes and the local DNS answer from that
snapshot. Managed host addresses therefore remain declared only in inventory,
and audit/rollback cannot pick up ambient host configuration from another
commit.
Ansible may establish those host primitives, but it does not own application
runtime configuration or activation.

Compose creates the shared `homelab_proxy` network and the stable explicitly
named `platform_traefik_data` and `platform_adguard_work` volumes on first
activation. The release engine intentionally never passes `--volumes` when it
stops a release, so those volumes and the host mounts retain durable state.
Ordinary rollback reactivates the previous complete package. Service policy is
declarative: AdGuard and Copyparty configuration is mounted read-only, and a
container restart reapplies the package-generated qBittorrent configuration.
UI preference edits are therefore not durable; downloads, filter work data,
and other application data remain durable.
