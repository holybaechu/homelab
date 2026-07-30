# Minecraft Retirement, Renovate Coverage, and VueTorrent Repair Implementation Plan

> **Recovery correction (2026-07-30):** Minecraft data cleanup is not an LXC
> retired-data-path task. Docker first proves no
> `com.docker.compose.project=game` containers remain, then `site.yml` runs
> `pve_retire_minecraft_data` on Proxmox for the exact host path
> `/var/lib/homelab/minecraft`. The role rechecks Docker through delegation and
> validates `/dev/pve/homelab-data` plus the complete descendant mount set.
> `prepare-low-id-cutover.yml` retains only the separate VMID 115/archive
> tombstone.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanently delete Minecraft from the repository and production homelab, restore VueTorrent for qBittorrent, and close all repository-controlled Renovate coverage gaps while making apt-managed services upgrade on deployment.

**Architecture:** Retire Minecraft with an idempotent VMID/archive tombstone and a separate post-Compose, Proxmox-host data tombstone before deleting its active declarations. Supply VueTorrent through the official pinned LinuxServer mod and select it through the managed qBittorrent configuration. Use focused Renovate managers for each nonstandard version surface and keep apt-owned package drift under Ansible rather than mislabeling it as Renovate coverage.

**Tech Stack:** Docker Compose, Ansible, OpenTofu, Renovate, GitHub Actions, Python/pytest, PowerShell and WSL validation.

## Global Constraints

- Minecraft deletion is irreversible and includes `/var/lib/homelab/minecraft` on Proxmox, VMID 115, and matching local `/var/lib/vz/dump/vzdump-lxc-115-*.tar.zst` archives.
- Destructive tasks must validate exact parents, hostnames, and VMIDs before deletion; no unresolved wildcard may be passed to `rm` or another destructive command.
- Preserve the active two-LXC architecture: VMID 110 `docker-apps` and VMID 111 `tailnet`.
- Minor and patch dependency updates automerge; major and `0.x` updates remain review-gated.
- Every Git-controlled version is Renovate-managed; apt-owned package versions are upgraded by Ansible with `state: latest`.
- Keep the existing Gluetun/qBittorrent shared network namespace and Proton port-forwarding behavior.
- Leave the pre-existing untracked `.pytest-tmp/` directory untouched.
- Use `apply_patch` for repository edits and stage only files belonging to the current task.

---

## File Structure

### Files removed

- `apps/compose/game/.env.example` — retired Minecraft environment example.
- `apps/compose/game/README.md` — retired Minecraft Compose documentation.
- `apps/compose/game/compose.yml` — Paper and Velocity services.
- `infra/ansible/roles/docker_compose_project/templates/game.env.j2` — retired Minecraft environment rendering.
- `infra/ansible/roles/docker_compose_project/templates/velocity.toml.j2` — retired Velocity configuration.
- `docs/runbooks/minecraft-server.md` — retired service runbook.
- `tests/docker/test_game_compose.py` — positive tests for a service that must no longer exist.

### Files created

- `infra/ansible/roles/pve_retire_minecraft/tasks/main.yml` — exact VMID 115 and local archive tombstone.
- `infra/ansible/roles/pve_retire_minecraft_data/` — post-Compose host-data tombstone with canonical device and mount-tree validation.
- `infra/ansible/files/assert-no-game-compose-containers.sh` — shared Docker runtime postcondition used by both hosts.
- `tests/infra/test_minecraft_retirement.py` — permanent retirement and cleanup-policy regression tests.

### Files modified

- `infra/ansible/inventory/prod/group_vars/all.yml` — remove Minecraft from generic legacy services.
- `infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml` — remove active game variables/project and add retired project/data tombstones.
- `infra/ansible/roles/docker_compose_project/tasks/main.yml` — stop retired projects and always assert that no game-labeled containers survive.
- `infra/ansible/roles/pve_homelab_storage/tasks/main.yml` — stop creating, migrating, chowning, or chmodding Minecraft data.
- `infra/ansible/playbooks/prepare-low-id-cutover.yml` — run only the VMID/archive Minecraft tombstone before shared-storage reconciliation.
- `infra/ansible/playbooks/site.yml` — run guarded Proxmox data deletion after the Docker Compose play succeeds.
- `infra/ansible/playbooks/validate.yml` — remove Minecraft checks and add VueTorrent checks.
- `apps/compose/media/compose.yml` — install the official pinned VueTorrent mod.
- `infra/ansible/roles/docker_compose_project/templates/qBittorrent.conf.j2` — select VueTorrent as the alternative Web UI.
- `renovate.json` — focused managers, custom datasources, and package rules.
- `.github/workflows/ci.yml` and `.github/workflows/cd.yml` — action version comments, Tailscale annotation, and OpenTofu deploy path.
- `.opentofu-version`, `infra/opentofu/envs/prod/containers.auto.tfvars`, and `apps/compose/hermes/Dockerfile` — custom-manager version surfaces.
- `requirements-dev.txt`, `infra/ansible/requirements.yml`, and `apps/compose/hermes/compose.yml` — exact dependency pins and nonduplicated local image tag.
- `infra/ansible/roles/common_debian/tasks/main.yml`, `infra/ansible/roles/docker_engine/tasks/main.yml`, and `infra/ansible/roles/tailscale_gateway/tasks/main.yml` — apt-owned runtime upgrades.
- Existing tests and runbooks that enumerate projects, templates, services, dependencies, or validation behavior.

---

### Task 1: Permanently Retire Minecraft

**Files:**

- Create: `tests/infra/test_minecraft_retirement.py`
- Create: `infra/ansible/roles/pve_retire_minecraft/tasks/main.yml`
- Modify: `infra/ansible/inventory/prod/group_vars/all.yml`
- Modify: `infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml`
- Modify: `infra/ansible/roles/docker_compose_project/tasks/main.yml`
- Modify: `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`
- Modify: `infra/ansible/playbooks/prepare-low-id-cutover.yml`
- Modify: `infra/ansible/playbooks/validate.yml`
- Modify: `tests/docker/test_docker_apps_topology.py`
- Modify: `tests/docker/test_compose_secrets.py`
- Modify: `tests/docker/test_docker_compose_project_role.py`
- Modify: `tests/repo/test_code_reduction.py`
- Delete: `apps/compose/game/.env.example`
- Delete: `apps/compose/game/README.md`
- Delete: `apps/compose/game/compose.yml`
- Delete: `infra/ansible/roles/docker_compose_project/templates/game.env.j2`
- Delete: `infra/ansible/roles/docker_compose_project/templates/velocity.toml.j2`
- Delete: `docs/runbooks/minecraft-server.md`
- Delete: `tests/docker/test_game_compose.py`

**Interfaces:**

- Consumes: `retired_docker_compose_projects`, `docker_compose_projects`, `/opt/homelab-compose`, and Proxmox `pct`/`vzdump` layout.
- Produces: roles `pve_retire_minecraft` and `pve_retire_minecraft_data`, a Docker label postcondition, and a three-project active Compose topology.

- [ ] **Step 1: Write the failing retirement-policy tests**

Create behavior tests that execute the shared Docker label guard with a
missing `compose.yml` and a surviving labeled container. Exercise the same
mountinfo/device validator used by the Proxmox deletion command with literal
fixtures for a valid ext4 LV root, a different device, a same-device bind
alias, a stacked exact-path bind, and same-filesystem descendant mounts.
Require full validation even when the target is already absent. Retain the
exact VMID 115/hostname/archive tombstone tests.

Update `tests/docker/test_docker_apps_topology.py` so `test_every_application_is_a_compose_project` loops over only `("platform", "media", "hermes")` and explicitly asserts `apps/compose/game` does not exist. Update `tests/docker/test_compose_secrets.py` to expect three `.env.example` files and remove `game.env.j2` from its template list.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/infra/test_minecraft_retirement.py tests/docker/test_docker_apps_topology.py tests/docker/test_compose_secrets.py -q
```

Expected: FAIL because `game` is still active, the new tombstones and role do not exist, and four environment examples remain.

- [ ] **Step 3: Add guarded runtime tombstones**

In `svc_docker_apps.yml`, retain `backup` and add `game` under
`retired_docker_compose_projects`. Remove `minecraft_version`,
`minecraft_memory`, `minecraft_max_players`, `minecraft_motd`, the active
`game` project, and every LXC-side retired data path.

After retired Compose reconciliation, always run the shared Docker guard. It
must query all containers by the exact
`com.docker.compose.project=game` label and fail on any returned ID, even when
the deployed Compose file is already absent.

Create `pve_retire_minecraft_data` for `/var/lib/homelab/minecraft`. Before
deletion, delegate the same Docker guard to `docker_apps`, prove the canonical
block-device identity of `/dev/pve/homelab-data`, require the mount's
filesystem root and ext4 type, and reject every mountpoint at or below the
target. Keep `rm --one-file-system` and make an already-absent target
idempotent only after all storage and Docker checks pass. Run this data role
from `site.yml` after the Docker Compose play with fatal host errors.

Create `pve_retire_minecraft/tasks/main.yml` with one guarded shell task:

```yaml
---
- name: Permanently remove retired Minecraft LXC and local archives
  ansible.builtin.shell: |
    set -eu
    vmid="115"
    expected_hostname="minecraft"
    archive_root="/var/lib/vz/dump"
    changed=0

    if pct config "$vmid" >/dev/null 2>&1; then
      actual_hostname="$(pct config "$vmid" | awk -F ': ' '$1 == "hostname" { print $2 }')"
      if [ "$actual_hostname" != "$expected_hostname" ]; then
        printf 'Refusing to destroy VMID %s: expected %s, found %s\n' \
          "$vmid" "$expected_hostname" "$actual_hostname" >&2
        exit 1
      fi
      if pct status "$vmid" | grep -q 'status: running'; then
        pct shutdown "$vmid" --timeout 120 || pct stop "$vmid"
      fi
      pct status "$vmid" | grep -q 'status: stopped'
      pct destroy "$vmid" --purge 1 --destroy-unreferenced-disks 1
      changed=1
    fi

    find "$archive_root" -maxdepth 1 -type f \
      -name 'vzdump-lxc-115-*.tar.zst' -print | while IFS= read -r archive; do
        case "$archive" in
          "$archive_root"/vzdump-lxc-115-*.tar.zst) ;;
          *) printf 'Refusing archive path: %s\n' "$archive" >&2; exit 1 ;;
        esac
        rm -f -- "$archive"
        printf 'archive-removed=yes\n'
      done > /tmp/homelab-minecraft-archive-cleanup
    if grep -q 'archive-removed=yes' /tmp/homelab-minecraft-archive-cleanup; then
      changed=1
    fi
    rm -f -- /tmp/homelab-minecraft-archive-cleanup

    if [ "$changed" -eq 1 ]; then
      echo changed=yes
    else
      echo changed=no
    fi
  args:
    executable: /bin/sh
  register: retired_minecraft_cleanup
  changed_when: "'changed=yes' in retired_minecraft_cleanup.stdout"
```

Run only the VMID/archive role `pve_retire_minecraft` in
`prepare-low-id-cutover.yml` before `pve_homelab_storage`. Remove
`{name: minecraft, vmid: 115}` from `legacy_lxcs`; bind-mounted data deletion
belongs exclusively to the later `site.yml` Proxmox play.

- [ ] **Step 4: Remove active Minecraft files and shared-storage behavior**

Delete all files listed under “Files removed.” Remove Minecraft directory creation, `chown`, and `find` arguments from the Docker application role and `pve_homelab_storage`. Remove all three Minecraft validation tasks from `validate.yml`.

Update active-project counts, template lists, and code-reduction lists in existing tests. Add all deleted paths to `test_retired_native_service_roles_and_scripts_are_removed` so accidental restoration fails policy checks.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```powershell
python -m pytest tests/infra/test_minecraft_retirement.py tests/docker/test_docker_apps_topology.py tests/docker/test_compose_secrets.py tests/docker/test_docker_compose_project_role.py tests/repo/test_code_reduction.py -q
```

Expected: PASS.

Commit:

```powershell
git add -- apps/compose/game docs/runbooks infra/ansible tests
git commit -m "Remove Minecraft service and data"
```

---

### Task 2: Restore VueTorrent Through the Official LinuxServer Mod

**Files:**

- Modify: `tests/docker/test_media_compose.py`
- Modify: `tests/docker/test_docker_apps_validate_playbook.py`
- Modify: `apps/compose/media/compose.yml`
- Modify: `infra/ansible/roles/docker_compose_project/templates/qBittorrent.conf.j2`
- Modify: `infra/ansible/playbooks/validate.yml`
- Modify: `apps/compose/media/README.md`

**Interfaces:**

- Consumes: LinuxServer `DOCKER_MODS`, qBittorrent alternative-Web-UI preferences, and existing Traefik `qbt` route.
- Produces: VueTorrent assets at `/vuetorrent/public`, managed selection in qBittorrent, and three-layer production validation.

- [ ] **Step 1: Write failing VueTorrent integration tests**

Add `import re` at the top of `tests/docker/test_media_compose.py`, then append:

```python
def test_qbittorrent_uses_pinned_official_vuetorrent_mod_and_managed_ui():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(encoding="utf-8")
    config = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/templates/qBittorrent.conf.j2"
    ).read_text(encoding="utf-8")

    qbittorrent = compose.split("  qbittorrent:", 1)[1].split("  copyparty:", 1)[0]
    assert re.search(
        r"^\s+DOCKER_MODS: ghcr\.io/vuetorrent/vuetorrent-lsio-mod:\d+\.\d+\.\d+$",
        qbittorrent,
        re.MULTILINE,
    )
    assert ":latest" not in qbittorrent
    assert "WebUI\\AlternativeUIEnabled=true" in config
    assert "WebUI\\RootFolder=/vuetorrent/public" in config
```

Append to `tests/docker/test_docker_apps_validate_playbook.py`:

```python
def test_validation_proves_vuetorrent_assets_config_and_route():
    validation = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(
        encoding="utf-8"
    )

    assert "test -f /vuetorrent/public/index.html" in validation
    assert "WebUI\\\\AlternativeUIEnabled=true" in validation
    assert "WebUI\\\\RootFolder=/vuetorrent/public" in validation
    assert "qbt.home.hchu.me" in validation
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/docker/test_media_compose.py tests/docker/test_docker_apps_validate_playbook.py -q
```

Expected: FAIL because the mod, qBittorrent preferences, and asset/config validation are absent.

- [ ] **Step 3: Add the mod and managed qBittorrent preferences**

Add under the qBittorrent service environment:

```yaml
      DOCKER_MODS: ghcr.io/vuetorrent/vuetorrent-lsio-mod:2.34.0
```

Add immediately after `WebUI\LocalHostAuth=false` in `qBittorrent.conf.j2`:

```ini
WebUI\AlternativeUIEnabled=true
WebUI\RootFolder=/vuetorrent/public
```

- [ ] **Step 4: Add production proof and documentation**

Add a validation task after the Compose-running check:

```yaml
    - name: Validate VueTorrent assets and qBittorrent selection
      ansible.builtin.shell: |
        set -eu
        docker compose exec -T qbittorrent test -f /vuetorrent/public/index.html
        docker compose exec -T qbittorrent grep -Fx \
          'WebUI\AlternativeUIEnabled=true' \
          /config/qBittorrent/qBittorrent.conf
        docker compose exec -T qbittorrent grep -Fx \
          'WebUI\RootFolder=/vuetorrent/public' \
          /config/qBittorrent/qBittorrent.conf
      args:
        executable: /bin/sh
        chdir: "{{ docker_apps_compose_root }}/media"
      changed_when: false
```

Keep the existing routed `curl` check for `qbt.home.hchu.me`; the new task proves the two internal layers. Update `apps/compose/media/README.md` to state that the official pinned mod supplies `/vuetorrent/public` and Ansible selects it.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```powershell
python -m pytest tests/docker/test_media_compose.py tests/docker/test_docker_apps_validate_playbook.py -q
```

Expected: PASS.

Commit:

```powershell
git add -- apps/compose/media infra/ansible/playbooks/validate.yml infra/ansible/roles/docker_compose_project/templates/qBittorrent.conf.j2 tests/docker
git commit -m "Fix VueTorrent for qBittorrent"
```

---

### Task 3: Close Renovate Coverage Gaps

**Files:**

- Modify: `tests/repo/test_renovate_updates.py`
- Modify: `tests/repo/test_github_workflows.py`
- Modify: `tests/docker/test_hermes_compose.py`
- Modify: `renovate.json`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/cd.yml`
- Modify: `scripts/ci/install-tools.sh`
- Modify: `scripts/ci/install-opentofu.sh`
- Modify: `requirements-dev.txt`
- Modify: `infra/ansible/requirements.yml`
- Modify: `apps/compose/hermes/Dockerfile`
- Modify: `apps/compose/hermes/compose.yml`

**Interfaces:**

- Consumes: Renovate built-in managers, regex custom managers, HTML custom datasources, GitHub release metadata, Docker tags, and repository version files.
- Produces: deterministic pins and a manager for every nonstandard repository-controlled version surface.

- [ ] **Step 1: Write failing coverage-policy tests**

Extend `tests/repo/test_renovate_updates.py` with:

```python
import re


def test_action_sha_pins_keep_release_comments_for_renovate():
    workflows = read(".github/workflows/ci.yml") + read(".github/workflows/cd.yml")
    action_lines = [line.strip() for line in workflows.splitlines() if "uses:" in line]
    sha_lines = [line for line in action_lines if re.search(r"@[0-9a-f]{40}\b", line)]

    assert sha_lines
    assert all(re.search(r"\s#\s+v?\d+(?:\.\d+){0,2}$", line) for line in sha_lines)


def test_nonstandard_version_surfaces_have_focused_managers():
    config = json.loads(read("renovate.json"))
    manager_text = json.dumps(config.get("customManagers", []))
    datasource_text = json.dumps(config.get("customDatasources", {}))

    for marker in (
        ".opentofu-version",
        "tailscale/tailscale",
        "vuetorrent-lsio-mod",
        "containers.auto.tfvars",
        "OP_CLI_VERSION",
    ):
        assert marker in manager_text
    assert "download.proxmox.com/images/system" in datasource_text
    assert "app-updates.agilebits.com/product_history/CLI2" in datasource_text


def test_direct_requirements_are_exact_and_local_hermes_tag_is_constant():
    requirements = read("requirements-dev.txt").splitlines()
    collection = read("infra/ansible/requirements.yml")
    compose = read("apps/compose/hermes/compose.yml")

    assert requirements == ["pytest==9.1.1", "Jinja2==3.1.6", "PyYAML==6.0.3"]
    assert 'version: "13.2.0"' in collection
    assert "image: homelab/hermes-agent:local" in compose
    assert "homelab/hermes-agent:2026" not in compose


def test_opentofu_updates_trigger_cd():
    assert '      - ".opentofu-version"' in read(".github/workflows/cd.yml")
```

Update `tests/docker/test_hermes_compose.py` to expect `homelab/hermes-agent:local` and `ARG OP_CLI_VERSION=2.35.0`.

- [ ] **Step 2: Run coverage tests and verify RED**

Run:

```powershell
python -m pytest tests/repo/test_renovate_updates.py tests/repo/test_github_workflows.py tests/docker/test_hermes_compose.py -q
```

Expected: FAIL for missing action comments/managers, open-ended dependencies, the duplicated Hermes tag, and missing CD filter.

- [ ] **Step 3: Normalize action and dependency pins**

Use these exact action comments:

```yaml
uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7
uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6
uses: tailscale/github-action@306e68a486fd2350f2bfc3b19fcd143891a4a2d8 # v4
```

Restore the Tailscale annotation immediately above `version: "1.98.4"`:

```yaml
# renovate: datasource=github-releases depName=tailscale/tailscale versioning=semver extractVersion=^v(?<version>.*)$
```

Set `requirements-dev.txt` to:

```text
pytest==9.1.1
Jinja2==3.1.6
PyYAML==6.0.3
```

Set the Ansible collection to `version: "13.2.0"`. Add `.opentofu-version` to CD paths. Remove the ineffective OpenTofu Renovate comments from `install-tools.sh` and `install-opentofu.sh`.

Change the Hermes Compose image to `homelab/hermes-agent:local`. In the Dockerfile add:

```dockerfile
ARG OP_CLI_VERSION=2.35.0
```

and install `1password-cli=${OP_CLI_VERSION}-1`. Keep the build argument as the
upstream semantic version Renovate discovers; append the repository's Debian
revision only at the APT install boundary.

- [ ] **Step 4: Replace the broad Renovate regex with focused managers**

Keep `config:recommended` and the existing automerge rule. Add custom managers with these exact responsibilities:

```json
{
  "customType": "regex",
  "description": "Update OpenTofu from the shared version file",
  "managerFilePatterns": ["/^\\.opentofu-version$/"],
  "matchStrings": ["^(?<currentValue>\\d+\\.\\d+\\.\\d+)\\s*$"],
  "depNameTemplate": "opentofu/opentofu",
  "datasourceTemplate": "github-releases",
  "versioningTemplate": "semver",
  "extractVersionTemplate": "^v(?<version>.*)$"
}
```

Add equivalent file-specific regex managers for:

- the annotated Tailscale `version` in `.github/workflows/cd.yml` using `github-releases`;
- the VueTorrent mod tag captured from `DOCKER_MODS` as the `currentValue` group using the Docker datasource;
- both Debian 13 template versions captured from `containers.auto.tfvars` as the `currentValue` group using `custom.proxmox-debian-13` and loose versioning;
- the value following `ARG OP_CLI_VERSION=` captured as the `currentValue` group using `custom.onepassword-cli` and semver.

Add:

```json
"customDatasources": {
  "proxmox-debian-13": {
    "defaultRegistryUrlTemplate": "https://download.proxmox.com/images/system/",
    "format": "html"
  },
  "onepassword-cli": {
    "defaultRegistryUrlTemplate": "https://app-updates.agilebits.com/product_history/CLI2",
    "format": "html"
  }
}
```

Add package rules that extract stable versions only:

```json
{
  "matchDatasources": ["custom.proxmox-debian-13"],
  "extractVersion": "^debian-13-standard_(?<version>.+)_amd64\\.tar\\.zst$"
},
{
  "matchDatasources": ["custom.onepassword-cli"],
  "extractVersion": ".*op_linux_amd64_v(?<version>\\d+\\.\\d+\\.\\d+)\\.zip$"
}
```

If the official Renovate validator shows that the HTML datasource exposes link text instead of hrefs, adjust only the corresponding `extractVersion` regex after inspecting debug extraction; do not broaden the manager file patterns.

- [ ] **Step 5: Verify configuration and GREEN**

Run:

```powershell
python -m json.tool renovate.json
python -m pytest tests/repo/test_renovate_updates.py tests/repo/test_github_workflows.py tests/docker/test_hermes_compose.py -q
npx --yes --package renovate -- renovate-config-validator
$env:LOG_LEVEL = "debug"
npx --yes --package renovate -- renovate --platform=local --dry-run=lookup
Remove-Item Env:LOG_LEVEL
```

Expected: JSON validation PASS, focused pytest PASS, Renovate reports valid configuration with no warnings about unknown fields or invalid regexes, and the local lookup log extracts OpenTofu, Tailscale, VueTorrent, both Proxmox template occurrences, and 1Password CLI.

- [ ] **Step 6: Commit**

```powershell
git add -- renovate.json .opentofu-version requirements-dev.txt infra/ansible/requirements.yml apps/compose/hermes .github/workflows scripts/ci tests/repo/test_renovate_updates.py tests/repo/test_github_workflows.py tests/docker/test_hermes_compose.py
git commit -m "Expand Renovate update coverage"
```

---

### Task 4: Upgrade Apt-Owned Dependencies During Deployment

**Files:**

- Modify: `tests/infra/test_service_hardening_review.py`
- Modify: `tests/docker/test_docker_engine_role.py`
- Modify: `tests/tailnet/test_tailscale_gateway_role.py`
- Modify: `infra/ansible/roles/common_debian/tasks/main.yml`
- Modify: `infra/ansible/roles/docker_engine/tasks/main.yml`
- Modify: `infra/ansible/roles/tailscale_gateway/tasks/main.yml`
- Modify: `infra/README.md`

**Interfaces:**

- Consumes: configured Debian, Docker, and Tailscale apt repositories.
- Produces: package upgrades on each deployment, with handlers controlling service restarts.

- [ ] **Step 1: Write failing runtime-update tests**

Add assertions that isolate each apt task and require `state: latest`:

```python
def test_apt_owned_runtime_dependencies_upgrade_on_deploy():
    common = (REPO_ROOT / "infra/ansible/roles/common_debian/tasks/main.yml").read_text(encoding="utf-8")
    docker = (REPO_ROOT / "infra/ansible/roles/docker_engine/tasks/main.yml").read_text(encoding="utf-8")
    tailscale = (REPO_ROOT / "infra/ansible/roles/tailscale_gateway/tasks/main.yml").read_text(encoding="utf-8")

    assert "Install Debian base packages" in common
    assert "state: latest" in common.split("- name: Set timezone", 1)[0]
    assert "state: latest" in docker.split("- name: Configure Docker daemon defaults", 1)[0]
    assert "state: latest" in tailscale.split("- name: Disable unusable public IPv6", 1)[0]
    assert "update_cache: true" in common
    assert "update_cache: true" in docker
    assert "update_cache: true" in tailscale
```

Place the test in `tests/infra/test_service_hardening_review.py`; add narrower role-specific assertions to the Docker and Tailscale test files if a mutation could otherwise change the wrong apt task.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/infra/test_service_hardening_review.py tests/docker/test_docker_engine_role.py tests/tailnet/test_tailscale_gateway_role.py -q
```

Expected: FAIL because the roles use `state: present`.

- [ ] **Step 3: Implement controlled apt upgrades**

Change only package-install tasks from `state: present` to `state: latest`:

- `Install Debian base packages`
- `Install Docker apt prerequisites`
- `Install Docker Engine and Compose plugin`
- `Install Tailscale`

Ensure the Docker Engine task includes `update_cache: true` and notifies `Restart Docker`; ensure the Tailscale package task notifies `Restart tailscaled after underlay change`. Do not change service/user/group desired-state tasks from `present`.

Update `infra/README.md` with the exact distinction: Renovate updates versions stored in Git; Ansible upgrades apt-owned dependencies during deployment.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```powershell
python -m pytest tests/infra/test_service_hardening_review.py tests/docker/test_docker_engine_role.py tests/tailnet/test_tailscale_gateway_role.py -q
```

Expected: PASS.

Commit:

```powershell
git add -- infra/ansible/roles/common_debian infra/ansible/roles/docker_engine infra/ansible/roles/tailscale_gateway infra/README.md tests/infra/test_service_hardening_review.py tests/docker/test_docker_engine_role.py tests/tailnet/test_tailscale_gateway_role.py
git commit -m "Upgrade apt managed dependencies on deploy"
```

---

### Task 5: Reconcile Runbooks and Complete Local Verification

**Files:**

- Modify: `apps/README.md`
- Modify: `docs/runbooks/docker-compose-migration.md`
- Modify: `docs/runbooks/github-actions.md`
- Modify: `docs/runbooks/proxmox-lxc-cutover.md`

**Interfaces:**

- Consumes: final repository topology and dependency policy.
- Produces: accurate operational documentation and local delivery evidence.

- [ ] **Step 1: Remove stale documentation references**

Run:

```powershell
rg -n -i "minecraft|compose/game|game\.env|velocity\.toml" apps docs infra tests .github secrets renovate.json
```

Expected after tombstones are considered: only the explicit retirement role/test/design/plan references remain. Remove active-service language from `apps/README.md` and `docker-compose-migration.md`. Update `github-actions.md` to document the minor/patch automerge boundary, major-review boundary, custom-manager surfaces, and apt-runtime distinction.

- [ ] **Step 2: Run the complete repository suite**

Run:

```powershell
$env:PYTHONPATH = "."
python -m pytest -q
```

Expected: all tests PASS with no warnings introduced by this change.

- [ ] **Step 3: Validate Compose and Ansible in WSL**

Run:

```powershell
wsl.exe -d Ubuntu --cd "/mnt/c/Users/holybaechu/Desktop/homelab" sh ./scripts/ci/validate-compose.sh
wsl.exe -d Ubuntu --cd "/mnt/c/Users/holybaechu/Desktop/homelab" env PYTHONPATH=. /home/holybaechu/.local/bin/uv run --isolated --with-requirements requirements-dev.txt --with-requirements requirements-deploy.txt ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/bootstrap.yml --syntax-check
wsl.exe -d Ubuntu --cd "/mnt/c/Users/holybaechu/Desktop/homelab" env PYTHONPATH=. /home/holybaechu/.local/bin/uv run --isolated --with-requirements requirements-dev.txt --with-requirements requirements-deploy.txt ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/prepare-low-id-cutover.yml --syntax-check
wsl.exe -d Ubuntu --cd "/mnt/c/Users/holybaechu/Desktop/homelab" env PYTHONPATH=. /home/holybaechu/.local/bin/uv run --isolated --with-requirements requirements-dev.txt --with-requirements requirements-deploy.txt ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check
wsl.exe -d Ubuntu --cd "/mnt/c/Users/holybaechu/Desktop/homelab" env PYTHONPATH=. /home/holybaechu/.local/bin/uv run --isolated --with-requirements requirements-dev.txt --with-requirements requirements-deploy.txt ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml --syntax-check
```

Expected: three Compose manifests validate and all four playbooks report successful syntax checks.

- [ ] **Step 4: Validate the final diff and commit documentation**

Run:

```powershell
git diff --check
git status --short
git diff --stat origin/main...HEAD
```

Confirm `.pytest-tmp/` is still untracked and no unrelated file is staged. Commit documentation changes:

```powershell
git add -- apps/README.md docs/runbooks
git commit -m "Document service retirement and update policy"
```

---

### Task 6: Deploy and Prove the Irreversible Cleanup

**Files:** None. If CI/CD reveals a defect, add only the focused test and production file required by the same red-green cycle.

**Interfaces:**

- Consumes: verified commits on `main`, GitHub CI/CD, production Proxmox, and Docker host.
- Produces: deleted Minecraft runtime/data, active VueTorrent, upgraded apt packages, and passing deployment evidence.

- [ ] **Step 1: Confirm destructive scope and push**

The conversation already explicitly authorizes permanent deletion. Before pushing, confirm the diff still targets only VMID 115, `/var/lib/homelab/minecraft` on Proxmox, `/opt/homelab-compose/game`, and `/var/lib/vz/dump/vzdump-lxc-115-*.tar.zst`.

Run:

```powershell
git status --short --branch
git log --oneline origin/main..HEAD
git push origin main
```

Expected: push succeeds without force and `.pytest-tmp/` remains untracked.

- [ ] **Step 2: Monitor CI and CD**

Resolve the new runs from the pushed head SHA and watch those exact IDs:

```powershell
$headSha = git rev-parse HEAD
$ciRunId = gh run list --workflow ci.yml --commit $headSha --limit 1 --json databaseId --jq '.[0].databaseId'
$cdRunId = gh run list --workflow cd.yml --commit $headSha --limit 1 --json databaseId --jq '.[0].databaseId'
if (-not $ciRunId -or -not $cdRunId) { throw "CI or CD run was not created for $headSha" }
gh run watch $ciRunId --exit-status
gh run watch $cdRunId --exit-status
```

Use the concrete run IDs returned by `gh run list`; do not watch an older run. Expected: both workflows complete successfully.

- [ ] **Step 3: Inspect deployment proof**

Run:

```powershell
$headSha = git rev-parse HEAD
$cdRunId = gh run list --workflow cd.yml --commit $headSha --limit 1 --json databaseId --jq '.[0].databaseId'
if (-not $cdRunId) { throw "CD run was not found for $headSha" }
gh run view $cdRunId --log
```

Confirm log evidence for:

- retired `game` Compose project removal;
- `/var/lib/homelab/minecraft` deletion on Proxmox after Docker's runtime postcondition;
- VMID 115 and matching local archive deletion or an idempotent already-absent result;
- VueTorrent asset/config validation;
- successful routed qBittorrent check;
- all remaining Compose services running.

- [ ] **Step 4: Repair and repeat on failure**

If CI or CD fails, use `superpowers:systematic-debugging`, identify the first failing boundary, add or correct a focused failing test, make the minimal fix, rerun all local verification, commit without amending previous commits, push, and monitor the new run IDs. Repeat until both workflows pass.

- [ ] **Step 5: Record final evidence**

Report the pushed commit SHA, CI URL, CD URL, pytest count, Compose validation count, Ansible syntax-check results, and the production proof for Minecraft absence and VueTorrent activation.
