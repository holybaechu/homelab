# Proxmox LXC Homelab Migration Design

Date: 2026-06-05
Status: Draft for user review

## Concise Recommendation

Migrate from Docker Compose stacks to five native Proxmox VE LXCs:

- `edge` on Alpine: Caddy reverse proxy and Cloudflare DDNS.
- `dns` on Alpine: AdGuard Home and its own DNS-01 ACME client.
- `tailnet` on Debian: Tailscale subnet router and optional exit node.
- `downloads` on Debian: native qBittorrent, WireGuard, NAT-PMP port forwarding, and firewall killswitch.
- `files` on Alpine: Copyparty serving explicit shares, including completed downloads read-only.

Replace Traefik with Caddy. Traefik's main advantage in the old repo was Docker provider labels. In the new static-LXC architecture, Caddy gives the simplest long-term edge layer: explicit routes, automatic HTTPS, fewer moving parts, and no Docker socket.

No preserved service should be a VM by default. Use a VM only as an escape hatch for `downloads` if WireGuard, NAT-PMP, or firewall behavior in an LXC proves unreliable.

## Current Repository Context

The current repo is small and Docker-centric:

- `stacks/traefik`: Traefik v3, Docker provider, Cloudflare ACME, dynamic middleware and external route files.
- `stacks/adguard`: AdGuard Home plus `traefik-certs-dumper`, DNS ports, DoT/DoQ ports, web UI, and DoH route.
- `stacks/tailscale`: Tailscale container advertising routes and exit-node capability.
- `stacks/qbittorrent`: Gluetun plus qBittorrent sharing Gluetun's network namespace.
- `stacks/copyparty`: Copyparty container with local users and mounted shares.
- `stacks/ddns`: Cloudflare DDNS updater container.
- `core/compose.yaml`: Komodo/Mongo/Periphery, not in preservation scope.
- `stacks/navidrome`, `stacks/openwebui`, and `stacks/minecraft`: present in the repo but not in preservation scope.

Only the six listed stacks are preserved. Other running or committed services are deprecated unless explicitly re-added later.

## Approaches Considered

| Approach | Verdict | Tradeoff |
|---|---|---|
| Caddy with native LXCs | Recommended | Best fit for known static upstreams, simple config, strong automatic HTTPS behavior, no Docker socket. Requires a Caddy build with the Cloudflare DNS provider module for DNS-01. |
| Native Traefik with file provider | Acceptable fallback | Keeps familiar routing concepts, but loses the Docker-provider advantage and remains more config-heavy than needed. |
| Docker-in-LXC hybrid | Avoid | Easiest way to keep Gluetun/qBittorrent behavior, but preserves the old operational model and makes the LXC migration less useful. |

## Final LXC Layout

| LXC | OS | Static IP example | Preserved responsibility | Why this OS |
|---|---|---:|---|---|
| `edge` | Alpine | `192.168.0.10` | Caddy, DDNS | Caddy and a small DDNS script are simple on Alpine. Low resource use and low package complexity. |
| `dns` | Alpine | `192.168.0.11` | AdGuard Home, DNS cert renewal | AdGuard ships as an upstream binary and works well as a small service. ACME DNS-01 clients are lightweight. |
| `tailnet` | Debian | `192.168.0.12` | Tailscale subnet router, optional exit node | Tailscale routing, TUN, forwarding, and systemd units are easier to operate and debug on Debian. |
| `downloads` | Debian | `192.168.0.13` | qBittorrent, Proton VPN, NAT-PMP, killswitch | VPN routing, nftables, WireGuard, NAT-PMP refresh, and qBittorrent Web API automation are complex enough to justify Debian. |
| `files` | Alpine | `192.168.0.14` | Copyparty | Copyparty is lightweight and can run cleanly under OpenRC using an installed package or Python venv. |

Recommended Proxmox tags: `homelab`, `managed-by-opentofu`, and one role tag such as `role=edge`.

Recommended startup order:

1. `dns`
2. `edge`
3. `tailnet`
4. `downloads`
5. `files`

## Service Installation Model

| Service | Installation | Service manager |
|---|---|---|
| Caddy | Custom build with Cloudflare DNS module, produced by Ansible or downloaded from a pinned release artifact | OpenRC on Alpine |
| Cloudflare DDNS | Small shell or Python script using Cloudflare API | OpenRC timer-like loop or cron |
| AdGuard Home | Upstream binary tarball pinned by version | OpenRC on Alpine |
| `lego` or `acme.sh` | Alpine package if acceptable, otherwise pinned upstream install | OpenRC/cron renewal job |
| Tailscale | Official Debian package repository | systemd |
| qBittorrent | Debian `qbittorrent-nox` package, or pinned upstream build if Debian version is too old | systemd |
| WireGuard | Debian `wireguard-tools` | systemd via `wg-quick@proton.service` |
| NAT-PMP updater | Custom script using `natpmpc` and qBittorrent Web API | systemd service/timer |
| nftables killswitch | Debian package | systemd |
| Copyparty | Alpine package if current enough, otherwise Python venv/pipx | OpenRC |

## Edge Design

Use Caddy as the only HTTP/HTTPS edge reverse proxy. Public router forwards TCP `80` and `443` to `edge`.

Caddy should proxy:

- `copyparty.hchu.me` publicly to `files:3923`.
- `dns.hchu.me/dns-query` publicly or semi-publicly to AdGuard DoH if encrypted DNS is enabled.
- `adguard.home.hchu.me` privately to AdGuard UI.
- `qbt.home.hchu.me` privately to qBittorrent Web UI.
- `pve.home.hchu.me`, `router.home.hchu.me`, and `printer.home.hchu.me` privately if those shortcuts are still desired.

Do not proxy DoT through ordinary Caddy HTTP reverse proxying. If Caddy layer4 is later added, treat it as a deliberate expansion, not part of the baseline.

Use Caddy request matchers for private routes. Allow LAN and Tailscale ranges:

- `192.168.0.0/24`
- `100.64.0.0/10`
- Optional RFC1918 ranges only if the network actually uses them.

Example Caddyfile shape:

```caddyfile
{
	email holybaechu@proton.me
	acme_dns cloudflare {$CLOUDFLARE_DNS_API_TOKEN}
}

(private_only) {
	@not_private not remote_ip 192.168.0.0/24 100.64.0.0/10
	respond @not_private 403
}

copyparty.hchu.me {
	reverse_proxy 192.168.0.14:3923
}

adguard.home.hchu.me {
	import private_only
	reverse_proxy 192.168.0.11:80
}

qbt.home.hchu.me {
	import private_only
	reverse_proxy 192.168.0.13:8080
}

dns.hchu.me {
	handle /dns-query {
		reverse_proxy https://192.168.0.11:443 {
			transport http {
				tls_insecure_skip_verify
			}
		}
	}
	respond 404
}
```

The implementation should avoid `tls_insecure_skip_verify` if AdGuard can present a cert trusted by Caddy for the upstream name. It is acceptable as an initial internal-only bridge when constrained by AdGuard's certificate serving behavior, but should be documented and limited to that upstream.

## AdGuard Design

AdGuard lives in `dns`.

Expose:

- TCP/UDP `53` on the LAN for normal DNS.
- TCP `853` directly to AdGuard for DoT if enabled.
- UDP `853` directly to AdGuard for DoQ only if enabled.
- HTTP/HTTPS UI only through `edge` and private access controls.
- DoH through `edge` at `https://dns.hchu.me/dns-query`.

AdGuard should manage its own certificate for `dns.hchu.me` using DNS-01. Prefer `lego` or `acme.sh` with a Cloudflare token scoped to edit only the relevant DNS zone. Use a narrow certificate such as `dns.hchu.me`, not a wildcard.

Do not dump or bind-mount Caddy's private certs into AdGuard. Sharing Caddy certs would couple two trust domains: compromise of `dns` would expose Caddy-managed private keys, and compromise of `edge` would affect DNS TLS material. Separate ACME keeps the blast radius small.

If cert sharing is ever chosen anyway, limit it by:

- Sharing only `dns.hchu.me`, never a wildcard.
- Using a read-only bind mount into `dns`.
- Running AdGuard as a non-root service user.
- Restricting file permissions so AdGuard can read only the specific cert and key.

## Downloads And VPN Design

`downloads` runs qBittorrent natively under Debian. It should not use Docker or Gluetun.

Core behavior:

- Use Proton VPN WireGuard configuration generated with NAT-PMP enabled on a P2P server.
- Start WireGuard before qBittorrent.
- Bind qBittorrent to the VPN interface.
- Use nftables as a killswitch: qBittorrent outbound traffic must not escape through the normal LXC interface.
- Allow qBittorrent Web UI only from `edge`, LAN admin clients, and Tailscale.
- Use `natpmpc` to request TCP and UDP mappings from Proton's NAT-PMP gateway and refresh before expiry.
- Parse the assigned external port and update qBittorrent through its Web API.
- Disable qBittorrent UPnP/NAT-PMP against the home router.

The NAT-PMP updater should be a systemd service with clear logs. It should fail closed: if NAT-PMP fails, it should set qBittorrent listen port to `0` or pause torrents depending on final preference.

## Copyparty Design

`files` runs Copyparty with the existing intent from `stacks/copyparty/copyparty.conf`:

- Preserve local user accounts, but move passwords to SOPS or GitHub Actions secrets, not command-line args in a Compose file.
- Preserve public and restricted shares that are explicitly still wanted.
- Add a completed-downloads share from `/srv/downloads`.
- Do not expose incomplete downloads.
- Prefer read-only access to completed downloads.

Current Korean path names in `copyparty.conf` appear mojibake in the checked-out file. Before migration, verify the intended Unicode names and rewrite the new config in UTF-8.

## Storage And Permissions

Create a shared Proxmox host dataset or directory:

```text
/tank/homelab/downloads/
  incomplete/
  complete/
```

Mounts:

| Host path | LXC | Guest path | Mode |
|---|---|---|---|
| `/tank/homelab/downloads` | `downloads` | `/downloads` | read-write |
| `/tank/homelab/downloads/complete` | `files` | `/srv/downloads` | read-only |

qBittorrent configuration:

- Incomplete path: `/downloads/incomplete`.
- Completed path: `/downloads/complete`.

Copyparty configuration:

- Expose `/srv/downloads` as a read-only share unless write access is explicitly needed.

Permission model:

- Use unprivileged LXCs where practical.
- Standardize a service UID/GID, for example `media:media`.
- On the Proxmox host, chown the bind-mounted dataset to the shifted UID/GID that maps to the service user inside the unprivileged containers.
- If simple shifted ownership is too confusing, use explicit Proxmox idmaps and document them in OpenTofu/Ansible.
- Avoid making `files` read-write to downloads. If upload/write shares are needed, create separate host directories for them.

## OpenTofu Structure

Use OpenTofu for infrastructure shape:

- LXC creation.
- OS templates.
- static IPs.
- CPU, RAM, root disk.
- tags and descriptions.
- startup order.
- mount points where provider permissions allow it.

Suggested structure:

```text
infra/
  opentofu/
    envs/
      prod/
        main.tf
        providers.tf
        variables.tf
        terraform.tfvars.example
        outputs.tf
    modules/
      pve-lxc/
        main.tf
        variables.tf
        outputs.tf
```

Module input example:

```hcl
lxcs = {
  edge = {
    vmid        = 110
    hostname    = "edge"
    os_template = "alpine-3.20-default"
    ip          = "192.168.0.10/24"
    gateway     = "192.168.0.1"
    cores       = 1
    memory_mb   = 512
    disk_gb     = 4
    tags        = ["homelab", "edge", "managed-by-opentofu"]
  }
}
```

The BPG Proxmox provider supports LXC containers and mount points, including arbitrary host bind mounts. Because arbitrary bind mounts may require elevated Proxmox privileges, the implementation should choose one of two patterns:

- If CI has sufficiently scoped Proxmox permissions: manage mount points in OpenTofu.
- If root-level permissions would be required: manage LXC creation in OpenTofu and apply host bind mounts through a Proxmox SSH Ansible role.

Do not store OpenTofu state in Git. Use a remote backend or an encrypted local state process appropriate for the environment.

## Ansible Structure

Use Ansible for in-LXC configuration and host-level finishing steps.

Suggested structure:

```text
infra/
  ansible/
    ansible.cfg
    inventory/
      prod/
        hosts.yml
        group_vars/
          all.yml
          alpine.yml
          debian.yml
          edge.yml
          dns.yml
          downloads.yml
          files.yml
          tailnet.yml
    playbooks/
      bootstrap.yml
      site.yml
      validate.yml
    roles/
      common_alpine/
      common_debian/
      pve_mounts/
      caddy/
      ddns/
      adguard/
      adguard_acme/
      tailscale_gateway/
      downloads_vpn/
      qbittorrent/
      copyparty/
```

Separate Alpine and Debian differences:

- Alpine roles use `apk`, OpenRC, `/etc/conf.d`, and service scripts.
- Debian roles use `apt`, systemd, `sysctl.d`, and `nftables`.
- Shared roles should expose variables, not shell out to distro-specific commands internally.

## GitHub Actions CD

Workflow goals:

- Join the private network using Tailscale before deployment.
- Deploy over SSH/API to Proxmox and/or LXCs.
- Keep secrets out of Git.
- Run validation after deployment.
- Disconnect or finish safely even on failure.

Suggested workflow:

```yaml
name: cd

on:
  workflow_dispatch:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: actions/checkout@v4

      - name: Connect Tailscale
        uses: tailscale/github-action@v4
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          audience: ${{ secrets.TS_AUDIENCE }}
          tags: tag:ci
          version: latest
          ping: ${{ vars.PVE_TAILSCALE_IP }}

      - name: Install tooling
        run: ./scripts/ci/install-tools.sh

      - name: Configure SSH
        run: ./scripts/ci/configure-ssh.sh

      - name: OpenTofu validate and plan
        run: ./scripts/ci/tofu-plan.sh

      - name: OpenTofu apply
        if: github.ref == 'refs/heads/main'
        run: ./scripts/ci/tofu-apply.sh

      - name: Ansible deploy
        run: ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml

      - name: Validate
        run: ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml

      - name: Tailscale logout
        if: always()
        run: sudo tailscale logout || true
```

Prefer Tailscale OAuth for CI over a long-lived reusable auth key. If an auth key is used, it should be tagged, reusable, ephemeral, pre-approved, and stored only in GitHub Actions secrets.

## Secrets

Do not commit secrets.

Recommended secret layout:

- GitHub Actions secrets for deployment-time credentials:
  - Tailscale OAuth client or auth key.
  - SSH private key for deployment.
  - Proxmox API token.
  - SOPS age key if CI decrypts secrets.
- SOPS with age for service secrets:
  - Cloudflare DNS token for Caddy.
  - Cloudflare DNS token for AdGuard ACME.
  - Cloudflare DDNS token.
  - Proton VPN WireGuard private key/config.
  - qBittorrent Web UI credentials.
  - Copyparty user passwords.
  - AdGuard admin credential material if managed.

Use separate Cloudflare tokens by purpose:

- `edge-caddy-acme`: DNS edit permission limited to ACME records for the zone where possible.
- `dns-adguard-acme`: DNS edit permission for `dns.hchu.me` validation.
- `edge-ddns`: DNS edit permission for only the records it updates where Cloudflare scoping allows.

## Network Plan

Static LAN IPs:

| Name | IP |
|---|---:|
| Proxmox host | `192.168.0.2` |
| `edge` | `192.168.0.10` |
| `dns` | `192.168.0.11` |
| `tailnet` | `192.168.0.12` |
| `downloads` | `192.168.0.13` |
| `files` | `192.168.0.14` |

Router port forwards:

- TCP `80` -> `edge:80`.
- TCP `443` -> `edge:443`.
- TCP `853` -> `dns:853` only if public DoT is required.
- UDP `853` -> `dns:853` only if public DoQ is required.

DNS behavior:

- LAN DHCP should point clients to `dns`.
- AdGuard DNS rewrites should map `*.home.hchu.me` to `edge`.
- Public records should point `copyparty.hchu.me` and `dns.hchu.me` to the public IP, maintained by DDNS if needed.
- Private admin names such as `qbt.home.hchu.me` and `adguard.home.hchu.me` should be resolvable only internally or harmless externally because Caddy denies non-private clients.

Tailscale behavior:

- `tailnet` advertises the LAN route, for example `192.168.0.0/24`.
- Exit node remains optional. Enable only if it is actually used.
- ACLs should permit `tag:ci` to reach only Proxmox SSH/API and deployment targets.

## Validation And Health Checks

Post-deploy validation should check:

- `caddy validate`.
- HTTPS status for public Copyparty.
- 403 behavior for private routes from a non-private source where practical.
- AdGuard DNS response through `dig @192.168.0.11`.
- DoH response at `https://dns.hchu.me/dns-query` if enabled.
- DoT TLS handshake to `dns.hchu.me:853` if enabled.
- Tailscale route advertisement and service status.
- qBittorrent Web UI only reachable from allowed networks.
- qBittorrent external IP is Proton VPN, not home WAN.
- qBittorrent listen port matches the current NAT-PMP assignment.
- Copyparty exposes completed downloads but not incomplete downloads.
- Copyparty read-only users cannot upload/delete from completed downloads.

## Backup And Rollback

Before migration:

- Back up current AdGuard config/work directories.
- Back up Copyparty config and any share data.
- Export qBittorrent config/state from the old container.
- Preserve Proton VPN configuration material securely.
- Snapshot or back up the old Docker VM/host state before shutting down services.

During deployment:

- Take Proxmox snapshots before stateful changes.
- Deploy LXCs first without cutting traffic.
- Move router port forwards only after health checks pass.
- Keep the old Docker deployment stopped but restorable until the new stack is stable.

Rollback:

- Restore previous Git SHA and re-run Ansible.
- Restore Proxmox snapshot for an affected LXC.
- Revert router port forwards to the old edge if needed.
- Restore service data from backups if a state migration fails.

## Migration Plan

1. Create the new repo structure without deleting old stacks yet.
2. Add OpenTofu module and prod environment for the five LXCs.
3. Add Ansible inventory and common Alpine/Debian roles.
4. Build `edge` with Caddy and static routes.
5. Build `dns` with AdGuard and independent `dns.hchu.me` ACME.
6. Migrate DNS clients or router DHCP to the new AdGuard.
7. Build `tailnet` as the Tailscale subnet router and validate GitHub Actions connectivity.
8. Build `downloads` with WireGuard, qBittorrent, NAT-PMP updater, and killswitch.
9. Mount shared storage into `downloads`; migrate qBittorrent config and test completed/incomplete paths.
10. Build `files` with Copyparty; migrate intended users/shares and mount completed downloads read-only.
11. Move public `80/443` router forwards to `edge`.
12. Enable DoT/DoQ router forwards only if needed.
13. Run full validation.
14. Stop old Docker stacks.
15. Archive old stack files under `legacy/docker-stacks/`.
16. Remove deprecated services from active deployment paths.

## Repository Restructure

Recommended final structure:

```text
.github/
  workflows/
    cd.yml
apps/
  edge/
    Caddyfile
    ddns/
  dns/
    AdGuardHome.yaml.example
    acme/
  downloads/
    qbittorrent/
    vpn/
    scripts/
  files/
    copyparty.conf
infra/
  opentofu/
    envs/prod/
    modules/pve-lxc/
  ansible/
    inventory/prod/
    playbooks/
    roles/
secrets/
  prod.sops.yml
scripts/
  ci/
legacy/
  docker-stacks/
docs/
  superpowers/specs/
```

## Old Files: Keep, Move, Drop

Keep as migration source material:

- `stacks/traefik/config/dynamic/middleware.yml`: local-only, rate-limit, and security-header policy intent.
- `stacks/traefik/config/dynamic/external.yml`: selected private admin shortcuts for Proxmox/router/printer.
- `stacks/adguard/compose.yaml`: port exposure and DoH/DoT intent.
- Existing AdGuard runtime config from the host, not currently committed.
- `stacks/tailscale/compose.yaml`: advertise-routes and exit-node intent.
- `stacks/qbittorrent/compose.yaml`: Proton VPN, port-forwarding behavior, qBittorrent Web API update intent.
- `stacks/copyparty/copyparty.conf`: share and account policy intent, after fixing encoding.
- `stacks/ddns/compose.yaml`: Cloudflare DDNS intent.

Move/archive:

- `stacks/traefik` -> `legacy/docker-stacks/traefik` after Caddy is live.
- `stacks/adguard` -> `legacy/docker-stacks/adguard` after `dns` is live.
- `stacks/tailscale` -> `legacy/docker-stacks/tailscale` after `tailnet` is live.
- `stacks/qbittorrent` -> `legacy/docker-stacks/qbittorrent` after `downloads` is live.
- `stacks/copyparty` -> `legacy/docker-stacks/copyparty` after `files` is live.
- `stacks/ddns` -> `legacy/docker-stacks/ddns` after DDNS is live in `edge`.

Drop from active deployment:

- Docker labels.
- Docker `proxy` network.
- Traefik Docker provider.
- Traefik cert dumper.
- Gluetun.
- `whoami`.
- DDNS updater UI.
- `core/compose.yaml` Komodo stack.
- `stacks/navidrome`.
- `stacks/openwebui`.
- `stacks/minecraft`.
- `dogtor.hchu.me` external route unless explicitly re-approved.

## Security Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Public Copyparty exposure | Strong passwords, narrow shares, read-only where possible, Caddy security headers/rate limits, logs, no incomplete download exposure. |
| qBittorrent traffic bypassing VPN | nftables killswitch, qBittorrent bound to `wg0`, external IP health check, fail-closed systemd dependencies. |
| Proton VPN credentials/config leak | Store only in SOPS/GitHub secrets, restrict file permissions, do not print configs in CI logs. |
| Cloudflare token leak | Separate least-privilege tokens for Caddy ACME, AdGuard ACME, and DDNS. Rotate on suspected exposure. |
| Tailscale auth material leak | Prefer OAuth with `tag:ci`; if auth key is used, make it ephemeral/tagged/pre-approved and rotate regularly. |
| SSH deployment key leak | Use a dedicated deploy key, restrict authorized_keys command/source where possible, rotate on suspicion. |
| Proxmox API token overreach | Use a dedicated token with the minimum required privileges; avoid giving CI root unless bind mount management requires it and is explicitly accepted. |
| AdGuard TLS private key leak | Use a narrow `dns.hchu.me` cert, store only in `dns`, run AdGuard as non-root, do not share Caddy cert cache. |
| Public DoT abuse | Enable only if needed, rate-limit at router/firewall where possible, monitor query volume. |
| Misconfigured private routes | Validate 403 behavior from outside allowed CIDRs and keep admin UIs off public DNS where possible. |

## References

- Caddy automatic HTTPS: https://caddyserver.com/docs/automatic-https
- Caddy reverse proxy: https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
- Caddy Cloudflare DNS module: https://caddyserver.com/docs/modules/dns.providers.cloudflare
- AdGuard Home DNS encryption: https://adguard-dns.io/kb/adguard-home/encryption/
- Tailscale GitHub Action: https://tailscale.com/docs/integrations/github/github-action
- Tailscale OAuth clients: https://tailscale.com/docs/features/oauth-clients
- Tailscale auth keys: https://tailscale.com/kb/1085/auth-keys
- BPG Proxmox LXC resource: https://registry.terraform.io/providers/bpg/proxmox/latest/docs/resources/virtual_environment_container
- Proton VPN manual port forwarding: https://protonvpn.com/support/port-forwarding-manual-setup/
- Proton VPN WireGuard Linux manual setup: https://protonvpn.com/support/wireguard-manual-linux
