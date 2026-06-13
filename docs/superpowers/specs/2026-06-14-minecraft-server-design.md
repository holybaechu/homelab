# Minecraft Server Design

## Context

The homelab repository currently manages services as Proxmox LXCs declared in OpenTofu and configured by Ansible. Active app configuration lives under `apps/`, while install and service management lives under `infra/ansible/roles/`. Older Docker stacks were intentionally removed during the LXC migration, so the Minecraft server should be added as a new managed LXC instead of restoring a Docker Compose stack.

The requested server is a public Minecraft server at `hchu.me` with:

- Velocity proxy.
- Paper backend.
- Geyser for Bedrock crossplay.
- Floodgate for Bedrock-only authentication.
- ViaVersion for Java client compatibility.
- A strict allowlist:
  - Java: `holybaechu`
  - Bedrock: `holybaechuwu`

Java access should use an SRV record for `hchu.me` that points at `home.hchu.me`. Bedrock access should expose the Geyser UDP port directly, because Bedrock clients do not use the Java SRV record behavior.

## Decisions

1. Add a dedicated Debian `minecraft` LXC managed by OpenTofu and Ansible.
2. Use the existing repo pattern: app-owned configuration under `apps/minecraft` and service automation under `infra/ansible/roles/minecraft`.
3. Use Velocity as the only public Java entrypoint on TCP `25565`.
4. Run Geyser and Floodgate as Velocity plugins.
5. Run Paper as a backend server bound to `127.0.0.1` on a private port such as `25566`.
6. Use Velocity modern forwarding between Velocity and Paper.
7. Generate and store the Velocity forwarding secret on the LXC; do not commit it.
8. Keep Velocity in online mode for Java authentication.
9. Configure Paper for Velocity forwarding and disable direct Paper online authentication as required by Velocity forwarding.
10. Install ViaVersion on the Paper backend only. For a single backend, this follows ViaVersion's guidance to install on either the proxy or backend servers, while keeping protocol translation closest to the server.
11. Do not include ViaBackwards or ViaRewind in the initial scope. Those can be added later if older Java clients must connect.
12. Accept the Minecraft EULA explicitly in the Ansible-managed Paper data directory.
13. Add a runbook covering DNS, router forwards, deployment, and join validation.

## Architecture

```text
Internet
  Java: _minecraft._tcp.hchu.me SRV -> home.hchu.me:25565
  Bedrock: hchu.me or home.hchu.me -> UDP 19132
        |
Router port forwards
        |
minecraft LXC, 192.168.0.8
  Velocity: 0.0.0.0:25565
    plugins:
      - Geyser-Velocity
      - Floodgate-Velocity
  Paper: 127.0.0.1:25566
    plugins:
      - ViaVersion
```

The Paper backend is not public. Binding it to localhost is the primary protection against bypassing Velocity. Velocity modern forwarding is still required because Paper must receive the correct player identity, UUID, and IP information from the proxy, but it is not treated as a firewall replacement.

## Components

### OpenTofu

Update `infra/opentofu/envs/prod/terraform.tfvars.example` with a `minecraft` LXC entry. The expected initial shape is:

- VMID: `115`
- Hostname: `minecraft`
- IP: `192.168.0.8/24`
- OS: Debian
- Tags: `homelab`, `managed-by-opentofu`, `role-minecraft`
- Root disk: `32G`
- CPU: `4` cores
- Memory: `4096MB`
- Swap: `1024MB`
- Startup order: `6`

These values intentionally size the LXC above the utility services because the world data, JVM heap, plugins, and logs all live with the Minecraft runtime.

### Ansible Inventory

Update `infra/ansible/inventory/prod/hosts.yml`:

- Add `minecraft` to the `debian` group.
- Add a dedicated `minecraft` group for role targeting.

Update `infra/ansible/inventory/prod/group_vars/all.yml`:

- Add `minecraft_ip: 192.168.0.8`.
- Add `minecraft` to `pve_lxc_access_bootstrap`.

Add `infra/ansible/inventory/prod/group_vars/minecraft.yml` for non-secret settings:

- Public Java port: `25565`
- Public Bedrock port: `19132`
- Paper backend port: `25566`
- Server memory flags
- Minecraft/Paper version and build inputs
- Velocity version input
- Plugin download inputs for Geyser, Floodgate, and ViaVersion
- Allowed Java players: `holybaechu`
- Allowed Bedrock players: `holybaechuwu`
- Floodgate username prefix: `.`

### Ansible Role

Add `infra/ansible/roles/minecraft` with tasks to:

- Install Java and required packages.
- Create a locked-down `minecraft` user and group.
- Create directories for Velocity, Paper, plugins, logs, and backups.
- Download Velocity, Paper, Geyser-Velocity, Floodgate-Velocity, and ViaVersion jars from versioned URLs or APIs.
- Generate the Velocity forwarding secret once and reuse it.
- Template `velocity.toml` and `forwarding.secret`.
- Template Paper `server.properties` with `server-ip=127.0.0.1`, backend port `25566`, and allowlist enabled.
- Template Paper forwarding config so `proxies.velocity.enabled` is true and the secret matches Velocity.
- Install ViaVersion into Paper's plugins directory.
- Install Geyser and Floodgate into Velocity's plugins directory.
- Configure Geyser auth as `floodgate`.
- Manage Java and Bedrock allowlist files from resolved UUIDs.
- Install `minecraft-paper.service` and `minecraft-velocity.service`.
- Start Paper before Velocity, with Velocity requiring Paper.

The services should run as the unprivileged `minecraft` user.

### App Configuration

Add `apps/minecraft` for source-controlled config inputs that are not secrets:

- `allowed-players.yml`

Full upstream config files should not be copied into `apps/minecraft` unless they are stable and small. The role should use focused Jinja templates for `server.properties`, `velocity.toml`, Geyser, and Floodgate config.

## Access

Public router forwards:

- TCP `25565` -> `192.168.0.8:25565`
- UDP `19132` -> `192.168.0.8:19132`

DNS:

- `home.hchu.me` resolves to the home public endpoint through the existing DDNS path.
- `_minecraft._tcp.hchu.me` SRV points to `home.hchu.me` on port `25565`.
- Bedrock players connect to `hchu.me` or `home.hchu.me` on UDP port `19132`.

The implementation should document exact Cloudflare record values in the runbook, but DNS records themselves are not managed by this repo unless existing infrastructure already manages them.

## Player Access Policy

Only these players should be admitted:

- Java: `holybaechu`
- Bedrock: `holybaechuwu`

Java authentication remains Mojang/Microsoft online authentication at Velocity. Bedrock authentication uses Floodgate so `holybaechuwu` can join without owning or linking a Java account.

The implementation should enforce this by generating Paper's `whitelist.json` from resolved identities:

- Resolve Java UUIDs from Mojang's profile API.
- Configure Floodgate with `username-prefix: "."`.
- Resolve Bedrock Floodgate UUIDs and prefixed names through GeyserMC's `/v2/utils/uuid/bedrock_or_java/:username?prefix=.` utility endpoint.
- Write only `holybaechu` and `.holybaechuwu` identities to `whitelist.json`.
- Keep `white-list=true` and `enforce-whitelist=true` in `server.properties`.

Velocity may still accept the connection briefly, but Paper must reject any unlisted Java or Bedrock identity before the player can join the world. The Bedrock user-facing gamertag remains `holybaechuwu`; the backend allowlist identity is `.holybaechuwu`.

## Secrets

Do not commit:

- Velocity forwarding secret.
- Floodgate `key.pem`.
- Any generated UUID cache if it contains runtime identity artifacts not meant to be source controlled.

The forwarding secret should be generated once on the target LXC and reused. Floodgate key material should stay in the Velocity plugin data directory unless backend Floodgate support is later added.

## Validation

Static tests should confirm:

- The `minecraft` LXC exists in `terraform.tfvars.example`.
- `minecraft_ip` is declared in group vars.
- `minecraft` is present in Ansible inventory and the Debian group.
- `site.yml` applies the `minecraft` role to the `minecraft` group.
- No Docker Compose Minecraft stack is reintroduced.
- The configured allowed players include `holybaechu` and `holybaechuwu`.
- Floodgate username prefix is `.`.
- Paper allowlist generation resolves `.holybaechuwu` through GeyserMC's UUID utility endpoint.
- ViaVersion is installed only in the Paper plugin path.
- Geyser and Floodgate are installed in the Velocity plugin path.
- Velocity uses modern forwarding.
- Paper binds to `127.0.0.1`.

Ansible validation should check:

- `minecraft-paper.service` is active.
- `minecraft-velocity.service` is active.
- TCP `25565` is listening.
- UDP `19132` is configured for Geyser.
- Paper backend port `25566` is not reachable externally.
- Required plugin jars exist.

Manual validation should check:

- Java client `holybaechu` can join through `hchu.me`.
- Bedrock client `holybaechuwu` can join through the Bedrock endpoint.
- An unlisted Java account is denied.
- An unlisted Bedrock account is denied.

## Runbook Scope

Add `docs/runbooks/minecraft-server.md` covering:

1. Apply the OpenTofu LXC change.
2. Run the Ansible site playbook for `minecraft`.
3. Add or verify router port forwards.
4. Add or verify Cloudflare DNS records:
   - `home.hchu.me` points to the home endpoint.
   - `_minecraft._tcp.hchu.me` SRV targets `home.hchu.me:25565`.
5. Run Ansible validation.
6. Join from Java as `holybaechu`.
7. Join from Bedrock as `holybaechuwu`.
8. Confirm unlisted players are rejected.

## References

- PaperMC Velocity forwarding: https://docs.papermc.io/velocity/player-information-forwarding/
- PaperMC Velocity security: https://docs.papermc.io/velocity/security/
- Geyser setup: https://geysermc.org/wiki/geyser/setup/
- Floodgate setup: https://geysermc.org/wiki/floodgate/setup/
- Floodgate default config: https://github.com/GeyserMC/Floodgate/blob/master/core/src/main/resources/config.yml
- GeyserMC UUID utility endpoint: https://geysermc.org/wiki/api/api.geysermc.org/global-api-web-api-utils-controller-get-bedrock-or-java-uuid/
- ViaVersion installation: https://github.com/ViaVersion/ViaVersion/wiki/Installation
