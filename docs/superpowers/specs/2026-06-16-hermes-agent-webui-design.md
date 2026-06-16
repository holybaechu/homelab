# Hermes Agent WebUI Design

## Context

The homelab repository manages services as Proxmox LXCs declared in OpenTofu and configured by Ansible. Runtime-facing app configuration lives under `apps/`, while install and service management lives under `infra/ansible/roles/`. Edge routing is centralized in `apps/edge/Caddyfile`.

The requested service is a self-hosted Hermes Agent installation with the community Hermes WebUI from `nesquena/hermes-webui`. The initial deployment should expose a browser UI, give Hermes a dedicated writable workspace, and leave provider/model setup to the first WebUI or CLI session.

## Decisions

1. Add a dedicated Debian LXC named `hermes`.
2. Use a native Debian service install instead of Docker-in-LXC.
3. Expose the WebUI at `https://hermes.home.hchu.me`.
4. Keep the Caddy route LAN/Tailscale-only with the existing `private_only` pattern.
5. Require `HERMES_WEBUI_PASSWORD`, supplied from secrets.
6. Mount a dedicated persistent workspace from `/var/lib/homelab/hermes/workspace` into the LXC as `/workspace`.
7. Keep Hermes state persistent and separate from the workspace.
8. Do not mount unrelated homelab datasets into the Hermes LXC in the initial version.
9. Do not manage provider/model API keys in Ansible. First-run setup writes those into persistent Hermes state.
10. Run only the WebUI service initially. Do not add a separate Hermes Gateway/API service unless a future integration needs it.

## Architecture

```text
LAN or Tailscale browser
        |
https://hermes.home.hchu.me
        |
edge LXC, Caddy
  private_only matcher
  secure headers
  reverse proxy -> 192.168.0.9:8787
        |
hermes LXC, Debian
  hermes-webui.service
  WebUI listens on 0.0.0.0:8787 inside the private LAN
  Hermes Agent used in-process by WebUI
  HERMES_HOME -> persistent Hermes state
  /workspace -> persistent writable workspace mount
```

Initial infrastructure shape:

- VMID: `116`
- Hostname: `hermes`
- IP: `192.168.0.9/24`
- OS: Debian
- Tags: `homelab`, `managed-by-opentofu`, `role-hermes`
- Suggested root disk: `16G`
- Suggested CPU: `2` cores
- Suggested memory: `2048MB`
- Suggested swap: `1024MB`
- Startup order: `7`

The sizing is intentionally larger than utility services because Hermes Agent, Python environments, cloned repos, WebUI state, logs, and long-running sessions can consume more disk and RAM than a small proxy or DNS service. It remains smaller than the Minecraft LXC because provider inference is not hosted locally in this scope.

## Components

### OpenTofu

Update `infra/opentofu/envs/prod/terraform.tfvars.example` with a `hermes` LXC entry using the shape above.

### Ansible Inventory

Update `infra/ansible/inventory/prod/hosts.yml`:

- Add `hermes` to the `debian` group.
- Add a dedicated `hermes` group.

Update `infra/ansible/inventory/prod/group_vars/all.yml`:

- Add `hermes_ip: 192.168.0.9`.
- Add `hermes` to `pve_lxc_access_bootstrap`.
- Add root-only Proxmox LXC mount settings for:
  - `/var/lib/homelab/hermes/home` mounted as the Hermes home path.
  - `/var/lib/homelab/hermes/workspace` mounted as `/workspace`.

Add `infra/ansible/inventory/prod/group_vars/hermes.yml` for non-secret settings:

- Hermes Agent source URL and ref.
- Hermes WebUI source URL and ref.
- Install paths.
- Service user and group.
- `HERMES_HOME`.
- `HERMES_WEBUI_HOST`.
- `HERMES_WEBUI_PORT`.
- `HERMES_WEBUI_STATE_DIR`.
- `HERMES_WEBUI_DEFAULT_WORKSPACE`.

`hermes_webui_password` is required but should be supplied through the normal secret path, not committed in group vars.

### Ansible Role

Add `infra/ansible/roles/hermes` with tasks to:

- Install required packages such as Python, Git, curl, and any build/runtime packages needed by Hermes Agent and WebUI.
- Create a locked-down service user and group.
- Create persistent Hermes state and workspace directories.
- Clone or update Hermes Agent and Hermes WebUI from configured refs.
- Run the WebUI/Hermes bootstrap or dependency install in a repeatable way.
- Render an environment file containing WebUI runtime settings.
- Fail if `hermes_webui_password` is missing or empty.
- Install `hermes-webui.service`.
- Restart WebUI when code refs, the env file, or the service unit changes.

The service should run as the unprivileged Hermes service user. Provider/model credentials remain a first-run task and are not written by Ansible.

### Edge Route

Update `apps/edge/Caddyfile`:

```caddyfile
hermes.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy 192.168.0.9:8787
}
```

The route should stay internal-only. The WebUI must listen on an address reachable from the edge LXC, so it cannot bind only to `127.0.0.1` inside the Hermes LXC. The WebUI password is still required because the application grants access to agent tools and a writable workspace.

### Playbooks

Update `infra/ansible/playbooks/site.yml` with a `Configure hermes LXC` play that applies the `hermes` role.

Update `infra/ansible/playbooks/validate.yml` with checks for:

- `systemctl is-active hermes-webui` on the Hermes LXC.
- `curl -fsS http://127.0.0.1:8787/health` on the Hermes LXC.
- `curl -fsSI --http1.1 --resolve "hermes.home.hchu.me:443:{{ edge_ip }}" https://hermes.home.hchu.me/` through Caddy, expecting a reachable login or application response and `Via: 1.1 Caddy`.

## Data Flow

1. A LAN or Tailscale client opens `https://hermes.home.hchu.me`.
2. Caddy applies the private network matcher and rejects non-private clients.
3. Caddy proxies accepted traffic to the Hermes LXC on port `8787`.
4. Hermes WebUI requires `HERMES_WEBUI_PASSWORD` before access.
5. WebUI uses Hermes Agent in-process with persistent Hermes state.
6. Workspace file operations occur under `/workspace`.
7. Provider/model setup occurs after deployment through WebUI onboarding or CLI setup and persists under `HERMES_HOME`.

The initial service has write access only to its Hermes state and dedicated workspace. Existing homelab datasets are out of scope.

## Error Handling

- Missing `hermes_webui_password` fails the Ansible role before service start.
- Missing workspace or state directories are created with the service user ownership before service start.
- WebUI binds to `0.0.0.0:8787` inside the Hermes LXC so Caddy can reach it at `192.168.0.9:8787`; Caddy remains the only supported browser entrypoint.
- Failed dependency installs or failed health checks should fail deployment loudly.
- Provider/model keys are not validated by deployment because they are not managed in this version.

## Testing

Add focused pytest coverage for:

- `terraform.tfvars.example` includes the `hermes` LXC with expected VMID, IP, and resource sizing.
- `hosts.yml` places `hermes` in Debian and in its own service group.
- `group_vars/all.yml` declares `hermes_ip`, LXC bootstrap, and the Hermes home/workspace bind mounts.
- `site.yml` applies the `hermes` role.
- `validate.yml` checks `hermes-webui` and `/health`.
- `apps/edge/Caddyfile` includes `hermes.home.hchu.me`, imports `private_only`, and proxies to the Hermes IP and WebUI port.
- `secrets/README.md` documents `hermes_webui_password`.

Implementation verification should run the focused tests first, then broader repo tests if shared files or common patterns are touched.

## Runbook Scope

Add `docs/runbooks/hermes-agent-webui.md` covering:

1. Apply the OpenTofu LXC change.
2. Run the Proxmox LXC root-options/bootstrap tasks if needed for the workspace mount.
3. Run the Ansible site playbook for `hermes`.
4. Run Ansible validation.
5. Open `https://hermes.home.hchu.me` from LAN or Tailscale.
6. Log in with the WebUI password.
7. Complete provider/model setup through WebUI onboarding or Hermes CLI.
8. Confirm `/workspace` is the default writable workspace.

## Non-Goals

- No Docker or Compose deployment.
- No Open WebUI deployment.
- No separate Hermes Gateway/API service.
- No provider/model API keys in Ansible.
- No mounts for unrelated homelab datasets.
- No public internet access to the WebUI.

## References

- Hermes Agent documentation: https://hermes-agent.nousresearch.com/docs/
- Hermes WebUI repository: https://github.com/nesquena/hermes-webui
- Hermes WebUI configuration and access: https://github.com/nesquena/hermes-webui#configuration--access
- Hermes Agent Open WebUI/API notes: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/open-webui
