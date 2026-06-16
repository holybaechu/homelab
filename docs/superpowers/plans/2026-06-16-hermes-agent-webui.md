# Hermes Agent WebUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native Debian LXC service for Hermes Agent with `nesquena/hermes-webui`, a dedicated writable workspace, and a private Caddy route at `hermes.home.hchu.me`.

**Architecture:** OpenTofu declares a new `hermes` LXC. Ansible provisions persistent host storage, LXC bind mounts, the native Hermes/WebUI service, and validation checks. Caddy exposes only the private LAN/Tailscale route while WebUI password authentication protects the agent UI.

**Tech Stack:** OpenTofu, Proxmox LXC, Ansible, Debian systemd, Python virtualenv, Caddy, pytest.

---

## File Structure

- Create `tests/hermes/test_hermes_infra.py`: static tests for the LXC declaration, inventory, host storage, secret wiring, and committed non-secret group vars.
- Create `tests/hermes/test_hermes_role.py`: static tests for the Hermes Ansible role, env template, service template, and handler.
- Create `tests/hermes/test_hermes_edge_and_runbook.py`: static tests for the Caddy route, site and validation playbooks, secrets docs, and runbook.
- Modify `tests/infra/test_lxc_resource_sizing.py`: extend the existing capacity-plan test to cover `hermes`.
- Modify `infra/opentofu/envs/prod/terraform.tfvars.example`: add the `hermes` LXC.
- Modify `infra/ansible/inventory/prod/hosts.yml`: add the `hermes` host to `debian` and to its own group.
- Modify `infra/ansible/inventory/prod/group_vars/all.yml`: add Hermes IP, UID/GID, Proxmox bind mounts, and bootstrap entry.
- Create `infra/ansible/inventory/prod/group_vars/hermes.yml`: non-secret Hermes service variables.
- Modify `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`: create and chown Hermes persistent host directories.
- Create `infra/ansible/roles/hermes/tasks/main.yml`: install packages, clone sources, install Python dependencies, render env/service files, and enable WebUI.
- Create `infra/ansible/roles/hermes/handlers/main.yml`: restart handler for WebUI.
- Create `infra/ansible/roles/hermes/templates/hermes-webui.env.j2`: WebUI environment file.
- Create `infra/ansible/roles/hermes/templates/hermes-webui.service.j2`: systemd service unit.
- Modify `.github/workflows/cd.yml`: pass `HERMES_WEBUI_PASSWORD` into generated Ansible extra vars.
- Modify `apps/edge/Caddyfile`: add `hermes.home.hchu.me`.
- Modify `infra/ansible/playbooks/site.yml`: apply the Hermes role.
- Modify `infra/ansible/playbooks/validate.yml`: validate WebUI service, health, and Caddy route.
- Modify `secrets/README.md`: document `hermes_webui_password`.
- Create `docs/runbooks/hermes-agent-webui.md`: deployment and first-login procedure.

## Task 1: Hermes Infrastructure Static Tests

**Files:**
- Create: `tests/hermes/test_hermes_infra.py`
- Modify: `tests/infra/test_lxc_resource_sizing.py`

- [ ] **Step 1: Create the Hermes infrastructure tests**

Create `tests/hermes/test_hermes_infra.py`:

```python
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def tfvars_container_body(tfvars_text: str, name: str) -> str:
    match = re.search(
        rf"^  {name} = \{{(?P<body>.*?)^  \}}",
        tfvars_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name} container block not found"
    return match.group("body")


def numeric_value(container_body: str, key: str) -> int:
    match = re.search(rf"^\s+{key}\s+=\s+(\d+)$", container_body, re.MULTILINE)
    assert match is not None, f"{key} not found"
    return int(match.group(1))


def test_hermes_lxc_is_declared_in_example_tfvars():
    tfvars = read("infra/opentofu/envs/prod/terraform.tfvars.example")
    body = tfvars_container_body(tfvars, "hermes")

    assert 'hostname         = "hermes"' in body
    assert 'description      = "Hermes Agent WebUI managed by OpenTofu and Ansible"' in body
    assert 'tags             = ["homelab", "managed-by-opentofu", "role-hermes"]' in body
    assert 'template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"' in body
    assert 'os_type          = "debian"' in body
    assert 'ip_address       = "192.168.0.9/24"' in body
    assert 'mac_address      = "02:00:00:BA:EC:09"' in body
    assert numeric_value(body, "vmid") == 116
    assert numeric_value(body, "root_disk_gb") == 16
    assert numeric_value(body, "cores") == 2
    assert numeric_value(body, "memory_mb") == 2048
    assert numeric_value(body, "swap_mb") == 1024
    assert numeric_value(body, "startup_order") == 7


def test_hermes_inventory_is_debian_host_and_role_group():
    hosts = read("infra/ansible/inventory/prod/hosts.yml")

    assert re.search(
        r"debian:\s*\n\s+hosts:.*hermes:\s*\n\s+ansible_host: 192\.168\.0\.9",
        hosts,
        re.DOTALL,
    )
    assert re.search(r"hermes:\s*\n\s+hosts:\s*\n\s+hermes:", hosts)


def test_hermes_all_group_vars_define_ip_ids_bootstrap_and_mounts():
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")

    assert re.search(r"^hermes_ip: 192\.168\.0\.9$", all_vars, re.MULTILINE)
    assert re.search(r"^hermes_service_uid: 1200$", all_vars, re.MULTILINE)
    assert re.search(r"^hermes_service_gid: 1200$", all_vars, re.MULTILINE)
    assert "  - vmid: 116\n    name: hermes\n    os_family: debian" in all_vars
    assert "  - vmid: 116\n    name: hermes" in all_vars
    assert "bind_mount_sources:" in all_vars
    assert "      - /var/lib/homelab/hermes/home" in all_vars
    assert "      - /var/lib/homelab/hermes/workspace" in all_vars
    assert "mp=/var/lib/hermes" in all_vars
    assert "mp=/workspace" in all_vars


def test_hermes_group_vars_are_non_secret_service_settings():
    group_vars = read("infra/ansible/inventory/prod/group_vars/hermes.yml")

    assert "hermes_user: hermes" in group_vars
    assert "hermes_group: hermes" in group_vars
    assert "hermes_home: /var/lib/hermes" in group_vars
    assert "hermes_workspace: /workspace" in group_vars
    assert "hermes_webui_host: 0.0.0.0" in group_vars
    assert "hermes_webui_port: 8787" in group_vars
    assert "https://github.com/NousResearch/hermes-agent.git" in group_vars
    assert "https://github.com/nesquena/hermes-webui.git" in group_vars
    assert "hermes_webui_password:" not in group_vars
    assert "API_SERVER_KEY" not in group_vars


def test_proxmox_storage_role_creates_hermes_host_directories():
    tasks = read("infra/ansible/roles/pve_homelab_storage/tasks/main.yml")

    assert '"${mount_path}/hermes/home"' in tasks
    assert '"${mount_path}/hermes/workspace"' in tasks
    assert "homelab_container_uid_offset + hermes_service_uid" in tasks
    assert "homelab_container_uid_offset + hermes_service_gid" in tasks
    assert '"${mount_path}/hermes"' in tasks


def test_cd_workflow_passes_hermes_password_to_ansible_extra_vars():
    workflow = read(".github/workflows/cd.yml")

    assert "HERMES_WEBUI_PASSWORD:" in workflow
    assert '"hermes_webui_password": os.environ["HERMES_WEBUI_PASSWORD"]' in workflow
    assert "HERMES_API_KEY" not in workflow
    assert "API_SERVER_KEY" not in workflow
```

- [ ] **Step 2: Extend the shared LXC sizing test**

In `tests/infra/test_lxc_resource_sizing.py`, replace the `expected` dictionary inside `assert_container_sizing` with:

```python
    expected = {
        "edge": {"root_disk_gb": 6, "cores": 1, "memory_mb": 512},
        "downloads": {"root_disk_gb": 8, "cores": 2, "memory_mb": 1024},
        "files": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "dns": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "tailnet": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "minecraft": {"root_disk_gb": 32, "cores": 4, "memory_mb": 4096},
        "hermes": {"root_disk_gb": 16, "cores": 2, "memory_mb": 2048},
    }
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes/test_hermes_infra.py tests/infra/test_lxc_resource_sizing.py --basetemp .pytest-basetemp\hermes-infra
```

Expected: FAIL because `hermes` is not declared yet, `group_vars/hermes.yml` is missing, and the workflow does not pass `HERMES_WEBUI_PASSWORD`.

- [ ] **Step 4: Commit the failing tests**

Run:

```powershell
git add tests/hermes/test_hermes_infra.py tests/infra/test_lxc_resource_sizing.py
git commit -m "test: add Hermes infrastructure guards"
```

## Task 2: Hermes LXC, Inventory, Storage, and Secret Wiring

**Files:**
- Modify: `infra/opentofu/envs/prod/terraform.tfvars.example`
- Modify: `infra/ansible/inventory/prod/hosts.yml`
- Modify: `infra/ansible/inventory/prod/group_vars/all.yml`
- Create: `infra/ansible/inventory/prod/group_vars/hermes.yml`
- Modify: `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`
- Modify: `.github/workflows/cd.yml`

- [ ] **Step 1: Add the OpenTofu LXC declaration**

In `infra/opentofu/envs/prod/terraform.tfvars.example`, add this `hermes` block after the `minecraft` block and before the closing `}` of `containers`:

```hcl

  hermes = {
    vmid             = 116
    hostname         = "hermes"
    description      = "Hermes Agent WebUI managed by OpenTofu and Ansible"
    tags             = ["homelab", "managed-by-opentofu", "role-hermes"]
    template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    os_type          = "debian"
    ip_address       = "192.168.0.9/24"
    mac_address      = "02:00:00:BA:EC:09"
    gateway          = "192.168.0.1"
    root_disk_gb     = 16
    cores            = 2
    memory_mb        = 2048
    swap_mb          = 1024
    startup_order    = 7
  }
```

- [ ] **Step 2: Add Hermes to Ansible inventory**

In `infra/ansible/inventory/prod/hosts.yml`, add `hermes` under `debian.hosts`:

```yaml
        hermes:
          ansible_host: 192.168.0.9
```

Add this dedicated service group near the other service groups:

```yaml
    hermes:
      hosts:
        hermes:
```

- [ ] **Step 3: Add Hermes shared variables and Proxmox mount settings**

In `infra/ansible/inventory/prod/group_vars/all.yml`, add this IP after `minecraft_ip`:

```yaml
hermes_ip: 192.168.0.9
```

Add these UID/GID values after `downloads_service_gid`:

```yaml
hermes_service_uid: 1200
hermes_service_gid: 1200
```

Add this `pve_lxc_root_options` item after the `files` item:

```yaml
  - vmid: 116
    name: hermes
    bind_mount_sources:
      - /var/lib/homelab/hermes/home
      - /var/lib/homelab/hermes/workspace
    settings:
      - description: mount Hermes home
        pattern: '^mp0: /var/lib/homelab/hermes/home,mp=/var/lib/hermes(,.*)?$'
        pct_args: '-mp0 /var/lib/homelab/hermes/home,mp=/var/lib/hermes'
      - description: mount Hermes workspace
        pattern: '^mp1: /var/lib/homelab/hermes/workspace,mp=/workspace(,.*)?$'
        pct_args: '-mp1 /var/lib/homelab/hermes/workspace,mp=/workspace'
```

Add this `pve_lxc_access_bootstrap` item after `minecraft`:

```yaml
  - vmid: 116
    name: hermes
    os_family: debian
```

- [ ] **Step 4: Create non-secret Hermes group vars**

Create `infra/ansible/inventory/prod/group_vars/hermes.yml`:

```yaml
hermes_user: hermes
hermes_group: hermes
hermes_uid: "{{ hermes_service_uid }}"
hermes_gid: "{{ hermes_service_gid }}"
hermes_home: /var/lib/hermes
hermes_workspace: /workspace

hermes_install_dir: /opt/hermes
hermes_agent_dir: "{{ hermes_install_dir }}/hermes-agent"
hermes_webui_dir: "{{ hermes_install_dir }}/hermes-webui"
hermes_venv_path: "{{ hermes_install_dir }}/venv"
hermes_env_path: /etc/hermes-webui.env

hermes_agent_repo: https://github.com/NousResearch/hermes-agent.git
hermes_agent_ref: main
hermes_webui_repo: https://github.com/nesquena/hermes-webui.git
hermes_webui_ref: master

hermes_webui_host: 0.0.0.0
hermes_webui_port: 8787
hermes_webui_state_dir: "{{ hermes_home }}/webui"
hermes_webui_default_workspace: "{{ hermes_workspace }}"
```

- [ ] **Step 5: Create Hermes host storage directories**

In `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`, add Hermes directories to the `mkdir -p` block:

```sh
      "${mount_path}/hermes/home" \
      "${mount_path}/hermes/workspace"
```

Add this chown after the existing downloads and Copyparty chown lines:

```sh
    chown -R {{ homelab_container_uid_offset + hermes_service_uid }}:{{ homelab_container_uid_offset + hermes_service_gid }} "${mount_path}/hermes"
```

Add these chmod paths to the existing chmod section:

```sh
    chmod 0755 "${mount_path}/hermes" "${mount_path}/hermes/home" "${mount_path}/hermes/workspace"
```

- [ ] **Step 6: Pass the Hermes WebUI password through CD**

In `.github/workflows/cd.yml`, add this job environment variable after `QBITTORRENT_WEBUI_PASSWORD`:

```yaml
      HERMES_WEBUI_PASSWORD: ${{ secrets.HERMES_WEBUI_PASSWORD }}
```

In the `mapping = { ... }` block, add this entry after `qbittorrent_webui_password`:

```python
              "hermes_webui_password": os.environ["HERMES_WEBUI_PASSWORD"],
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes/test_hermes_infra.py tests/infra/test_lxc_resource_sizing.py --basetemp .pytest-basetemp\hermes-infra
```

Expected: PASS.

- [ ] **Step 8: Commit the infrastructure implementation**

Run:

```powershell
git add infra/opentofu/envs/prod/terraform.tfvars.example infra/ansible/inventory/prod/hosts.yml infra/ansible/inventory/prod/group_vars/all.yml infra/ansible/inventory/prod/group_vars/hermes.yml infra/ansible/roles/pve_homelab_storage/tasks/main.yml .github/workflows/cd.yml
git commit -m "feat: add Hermes LXC infrastructure"
```

## Task 3: Hermes Ansible Role Static Tests

**Files:**
- Create: `tests/hermes/test_hermes_role.py`

- [ ] **Step 1: Create role tests**

Create `tests/hermes/test_hermes_role.py`:

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_hermes_role_installs_native_service_without_docker_or_provider_keys():
    tasks = read("infra/ansible/roles/hermes/tasks/main.yml")

    assert "hermes_webui_password is defined" in tasks
    assert "hermes_webui_password | length > 0" in tasks
    assert "python3-venv" in tasks
    assert "python3-pip" in tasks
    assert "build-essential" in tasks
    assert "name: \"{{ hermes_agent_repo }}\"" in tasks
    assert "version: \"{{ hermes_agent_ref }}\"" in tasks
    assert "name: \"{{ hermes_webui_repo }}\"" in tasks
    assert "version: \"{{ hermes_webui_ref }}\"" in tasks
    assert "requirements: \"{{ hermes_webui_dir }}/requirements.txt\"" in tasks
    assert "src: hermes-webui.env.j2" in tasks
    assert "src: hermes-webui.service.j2" in tasks
    assert "name: hermes-webui.service" in tasks
    assert "docker" not in tasks.lower()
    assert "API_SERVER_KEY" not in tasks


def test_hermes_env_template_contains_webui_runtime_settings_only():
    env_template = read("infra/ansible/roles/hermes/templates/hermes-webui.env.j2")

    assert "HERMES_HOME={{ hermes_home | quote }}" in env_template
    assert "HERMES_CONFIG_PATH={{ (hermes_home + '/config.yaml') | quote }}" in env_template
    assert "HERMES_WEBUI_AGENT_DIR={{ hermes_agent_dir | quote }}" in env_template
    assert "HERMES_WEBUI_PYTHON={{ (hermes_venv_path + '/bin/python') | quote }}" in env_template
    assert "HERMES_WEBUI_HOST={{ hermes_webui_host | quote }}" in env_template
    assert "HERMES_WEBUI_PORT={{ hermes_webui_port | string | quote }}" in env_template
    assert "HERMES_WEBUI_STATE_DIR={{ hermes_webui_state_dir | quote }}" in env_template
    assert "HERMES_WEBUI_DEFAULT_WORKSPACE={{ hermes_webui_default_workspace | quote }}" in env_template
    assert "HERMES_WEBUI_PASSWORD={{ hermes_webui_password | quote }}" in env_template
    assert "API_SERVER_KEY" not in env_template
    assert "OPENAI_API_KEY" not in env_template


def test_hermes_service_template_runs_webui_from_agent_venv():
    service = read("infra/ansible/roles/hermes/templates/hermes-webui.service.j2")

    assert "Description=Hermes Agent WebUI" in service
    assert "User={{ hermes_user }}" in service
    assert "Group={{ hermes_group }}" in service
    assert "EnvironmentFile={{ hermes_env_path }}" in service
    assert "WorkingDirectory={{ hermes_agent_dir }}" in service
    assert "ExecStart={{ hermes_venv_path }}/bin/python {{ hermes_webui_dir }}/server.py" in service
    assert "Restart=on-failure" in service
    assert "WantedBy=multi-user.target" in service


def test_hermes_handler_restarts_webui_service():
    handlers = read("infra/ansible/roles/hermes/handlers/main.yml")

    assert "- name: Restart hermes-webui" in handlers
    assert "name: hermes-webui.service" in handlers
    assert "state: restarted" in handlers
    assert "daemon_reload: true" in handlers
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes/test_hermes_role.py --basetemp .pytest-basetemp\hermes-role
```

Expected: FAIL because `infra/ansible/roles/hermes` does not exist yet.

- [ ] **Step 3: Commit the failing role tests**

Run:

```powershell
git add tests/hermes/test_hermes_role.py
git commit -m "test: add Hermes role guards"
```

## Task 4: Hermes Ansible Role Implementation

**Files:**
- Create: `infra/ansible/roles/hermes/tasks/main.yml`
- Create: `infra/ansible/roles/hermes/handlers/main.yml`
- Create: `infra/ansible/roles/hermes/templates/hermes-webui.env.j2`
- Create: `infra/ansible/roles/hermes/templates/hermes-webui.service.j2`

- [ ] **Step 1: Create the Hermes role tasks**

Create `infra/ansible/roles/hermes/tasks/main.yml`:

```yaml
---
- name: Require Hermes WebUI password
  ansible.builtin.assert:
    that:
      - hermes_webui_password is defined
      - hermes_webui_password | length > 0
    fail_msg: hermes_webui_password must be provided through secrets.
  no_log: true

- name: Install Hermes runtime packages
  ansible.builtin.apt:
    name:
      - build-essential
      - ca-certificates
      - curl
      - git
      - python3
      - python3-pip
      - python3-venv
    update_cache: true
    state: present

- name: Create Hermes group
  ansible.builtin.group:
    name: "{{ hermes_group }}"
    gid: "{{ hermes_gid }}"
    state: present

- name: Create Hermes user
  ansible.builtin.user:
    name: "{{ hermes_user }}"
    uid: "{{ hermes_uid }}"
    group: "{{ hermes_group }}"
    shell: /usr/sbin/nologin
    create_home: false
    home: "{{ hermes_home }}"
    state: present

- name: Create Hermes install directory
  ansible.builtin.file:
    path: "{{ hermes_install_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Create Hermes persistent directories
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: "{{ hermes_user }}"
    group: "{{ hermes_group }}"
    mode: "0755"
  loop:
    - "{{ hermes_home }}"
    - "{{ hermes_webui_state_dir }}"
    - "{{ hermes_workspace }}"

- name: Clone Hermes Agent
  ansible.builtin.git:
    repo: "{{ hermes_agent_repo }}"
    dest: "{{ hermes_agent_dir }}"
    version: "{{ hermes_agent_ref }}"
    update: true
  notify: Restart hermes-webui

- name: Clone Hermes WebUI
  ansible.builtin.git:
    repo: "{{ hermes_webui_repo }}"
    dest: "{{ hermes_webui_dir }}"
    version: "{{ hermes_webui_ref }}"
    update: true
  notify: Restart hermes-webui

- name: Create Hermes Python virtualenv
  ansible.builtin.command:
    cmd: python3 -m venv "{{ hermes_venv_path }}"
    creates: "{{ hermes_venv_path }}/bin/python"

- name: Upgrade Hermes virtualenv packaging tools
  ansible.builtin.pip:
    name:
      - pip
      - setuptools
      - wheel
    state: latest
    virtualenv: "{{ hermes_venv_path }}"

- name: Install Hermes Agent into virtualenv
  ansible.builtin.pip:
    name: "file://{{ hermes_agent_dir }}"
    editable: true
    virtualenv: "{{ hermes_venv_path }}"
  notify: Restart hermes-webui

- name: Install Hermes WebUI requirements into virtualenv
  ansible.builtin.pip:
    requirements: "{{ hermes_webui_dir }}/requirements.txt"
    virtualenv: "{{ hermes_venv_path }}"
  notify: Restart hermes-webui

- name: Install Hermes WebUI environment
  ansible.builtin.template:
    src: hermes-webui.env.j2
    dest: "{{ hermes_env_path }}"
    owner: root
    group: root
    mode: "0600"
  no_log: true
  notify: Restart hermes-webui

- name: Install Hermes WebUI systemd service
  ansible.builtin.template:
    src: hermes-webui.service.j2
    dest: /etc/systemd/system/hermes-webui.service
    owner: root
    group: root
    mode: "0644"
  notify: Restart hermes-webui

- name: Enable Hermes WebUI
  ansible.builtin.systemd:
    name: hermes-webui.service
    daemon_reload: true
    enabled: true
    state: started
```

- [ ] **Step 2: Create the restart handler**

Create `infra/ansible/roles/hermes/handlers/main.yml`:

```yaml
---
- name: Restart hermes-webui
  ansible.builtin.systemd:
    name: hermes-webui.service
    state: restarted
    daemon_reload: true
```

- [ ] **Step 3: Create the environment template**

Create `infra/ansible/roles/hermes/templates/hermes-webui.env.j2`:

```jinja
HERMES_HOME={{ hermes_home | quote }}
HERMES_CONFIG_PATH={{ (hermes_home + '/config.yaml') | quote }}
HERMES_WEBUI_AGENT_DIR={{ hermes_agent_dir | quote }}
HERMES_WEBUI_PYTHON={{ (hermes_venv_path + '/bin/python') | quote }}
HERMES_WEBUI_HOST={{ hermes_webui_host | quote }}
HERMES_WEBUI_PORT={{ hermes_webui_port | string | quote }}
HERMES_WEBUI_STATE_DIR={{ hermes_webui_state_dir | quote }}
HERMES_WEBUI_DEFAULT_WORKSPACE={{ hermes_webui_default_workspace | quote }}
HERMES_WEBUI_PASSWORD={{ hermes_webui_password | quote }}
```

- [ ] **Step 4: Create the systemd service template**

Create `infra/ansible/roles/hermes/templates/hermes-webui.service.j2`:

```systemd
[Unit]
Description=Hermes Agent WebUI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ hermes_user }}
Group={{ hermes_group }}
EnvironmentFile={{ hermes_env_path }}
WorkingDirectory={{ hermes_agent_dir }}
ExecStart={{ hermes_venv_path }}/bin/python {{ hermes_webui_dir }}/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes/test_hermes_role.py --basetemp .pytest-basetemp\hermes-role
```

Expected: PASS.

- [ ] **Step 6: Commit the role implementation**

Run:

```powershell
git add infra/ansible/roles/hermes/tasks/main.yml infra/ansible/roles/hermes/handlers/main.yml infra/ansible/roles/hermes/templates/hermes-webui.env.j2 infra/ansible/roles/hermes/templates/hermes-webui.service.j2
git commit -m "feat: add Hermes WebUI role"
```

## Task 5: Edge, Playbook, Secret Docs, and Runbook Static Tests

**Files:**
- Create: `tests/hermes/test_hermes_edge_and_runbook.py`

- [ ] **Step 1: Create edge and runbook tests**

Create `tests/hermes/test_hermes_edge_and_runbook.py`:

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_caddyfile_exposes_hermes_as_private_route():
    caddyfile = read("apps/edge/Caddyfile")

    block = caddyfile.split("hermes.home.hchu.me {", maxsplit=1)[1].split(
        "pve.home.hchu.me {", maxsplit=1
    )[0]
    assert "import private_only" in block
    assert "import secure_headers" in block
    assert "reverse_proxy 192.168.0.9:8787" in block


def test_site_playbook_applies_hermes_role():
    site = read("infra/ansible/playbooks/site.yml")

    assert "- name: Configure hermes LXC" in site
    assert "hosts: hermes" in site
    assert "    - hermes" in site


def test_validate_playbook_checks_hermes_service_health_and_caddy_route():
    validate = read("infra/ansible/playbooks/validate.yml")

    assert "- name: Validate hermes" in validate
    assert "hosts: hermes" in validate
    assert "cmd: systemctl is-active hermes-webui" in validate
    assert "http://127.0.0.1:{{ hermes_webui_port }}/health" in validate
    assert "hermes.home.hchu.me:443:{{ edge_ip }}" in validate
    assert "https://hermes.home.hchu.me/login" in validate
    assert "Via: 1.1 Caddy" in validate


def test_secrets_readme_documents_hermes_password():
    secrets = read("secrets/README.md")

    assert "hermes_webui_password" in secrets
    assert "Hermes provider" not in secrets


def test_hermes_runbook_documents_deploy_validate_and_first_login():
    runbook = read("docs/runbooks/hermes-agent-webui.md")

    assert "https://hermes.home.hchu.me" in runbook
    assert "infra/ansible/playbooks/bootstrap.yml" in runbook
    assert "infra/ansible/playbooks/site.yml" in runbook
    assert "infra/ansible/playbooks/validate.yml" in runbook
    assert "HERMES_WEBUI_PASSWORD" in runbook
    assert "/workspace" in runbook
    assert "provider/model setup" in runbook
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes/test_hermes_edge_and_runbook.py --basetemp .pytest-basetemp\hermes-edge
```

Expected: FAIL because the route, playbook entries, secrets docs, and runbook are not implemented yet.

- [ ] **Step 3: Commit the failing edge and runbook tests**

Run:

```powershell
git add tests/hermes/test_hermes_edge_and_runbook.py
git commit -m "test: add Hermes edge and runbook guards"
```

## Task 6: Edge Route, Playbooks, Secret Docs, and Runbook

**Files:**
- Modify: `apps/edge/Caddyfile`
- Modify: `infra/ansible/playbooks/site.yml`
- Modify: `infra/ansible/playbooks/validate.yml`
- Modify: `secrets/README.md`
- Create: `docs/runbooks/hermes-agent-webui.md`

- [ ] **Step 1: Add the private Caddy route**

In `apps/edge/Caddyfile`, add this block between `qbt.home.hchu.me` and `pve.home.hchu.me`:

```caddyfile
hermes.home.hchu.me {
	import private_only
	import secure_headers
	reverse_proxy 192.168.0.9:8787
}

```

- [ ] **Step 2: Add Hermes to the site playbook**

In `infra/ansible/playbooks/site.yml`, add this play after the Minecraft play:

```yaml
- name: Configure hermes LXC
  hosts: hermes
  gather_facts: true
  roles:
    - hermes
```

- [ ] **Step 3: Add Hermes validation checks**

In `infra/ansible/playbooks/validate.yml`, add this task to the existing `Validate edge` play after the Caddy service check:

```yaml
    - name: Check Hermes WebUI is served through Caddy TLS route
      ansible.builtin.shell:
        cmd: |
          set -eu
          headers="$(curl -fsSI --http1.1 --resolve "hermes.home.hchu.me:443:{{ edge_ip }}" https://hermes.home.hchu.me/login)"
          printf '%s\n' "$headers" | grep -E "HTTP/1.1 (200|302)"
          printf '%s\n' "$headers" | grep -F "Via: 1.1 Caddy"
        executable: /bin/sh
      changed_when: false
```

Add this new play after `Validate minecraft service`:

```yaml
- name: Validate hermes
  hosts: hermes
  gather_facts: false
  tasks:
    - name: Check Hermes WebUI service
      ansible.builtin.command:
        cmd: systemctl is-active hermes-webui
      changed_when: false

    - name: Check Hermes WebUI port
      ansible.builtin.wait_for:
        host: 127.0.0.1
        port: "{{ hermes_webui_port }}"
        timeout: 5

    - name: Check Hermes WebUI health endpoint
      ansible.builtin.command:
        cmd: curl -fsS http://127.0.0.1:{{ hermes_webui_port }}/health
      changed_when: false
```

- [ ] **Step 4: Document the Hermes secret**

In `secrets/README.md`, add this expected encrypted value after `qbittorrent_webui_password`:

```markdown
- `hermes_webui_password`
```

- [ ] **Step 5: Create the runbook**

Create `docs/runbooks/hermes-agent-webui.md`:

```markdown
# Hermes Agent WebUI

Hermes Agent WebUI runs in the `hermes` Debian LXC and is exposed only through the private Caddy route at `https://hermes.home.hchu.me`.

## Secrets

Set `HERMES_WEBUI_PASSWORD` in GitHub Actions secrets. The CD workflow writes it to Ansible as `hermes_webui_password`.

Provider/model API keys are not deployed by this repo. Complete provider/model setup from the WebUI onboarding flow or from the Hermes CLI after the service is running.

## Deploy

1. Apply the OpenTofu LXC changes:

   ```sh
   ./scripts/ci/tofu-plan.sh
   ./scripts/ci/tofu-apply.sh
   ```

2. Bootstrap Proxmox storage, root-only bind mounts, SSH, and base packages:

   ```sh
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/bootstrap.yml
   ```

3. Deploy services:

   ```sh
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --extra-vars @/tmp/ansible-extra-vars.json
   ```

4. Validate services:

   ```sh
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml
   ```

## First Login

1. Open `https://hermes.home.hchu.me` from LAN or Tailscale.
2. Log in with `HERMES_WEBUI_PASSWORD`.
3. Complete provider/model setup in the WebUI onboarding flow or with Hermes CLI.
4. Confirm `/workspace` is selected as the default writable workspace.

## Storage

Persistent host paths:

- `/var/lib/homelab/hermes/home` is mounted inside the LXC as `/var/lib/hermes`.
- `/var/lib/homelab/hermes/workspace` is mounted inside the LXC as `/workspace`.

The Hermes service does not mount unrelated homelab datasets.
```

- [ ] **Step 6: Run focused tests**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes/test_hermes_edge_and_runbook.py tests/edge/test_caddyfile.py tests/edge/test_validate_playbook.py --basetemp .pytest-basetemp\hermes-edge
```

Expected: PASS.

- [ ] **Step 7: Commit the edge and docs implementation**

Run:

```powershell
git add apps/edge/Caddyfile infra/ansible/playbooks/site.yml infra/ansible/playbooks/validate.yml secrets/README.md docs/runbooks/hermes-agent-webui.md
git commit -m "feat: expose Hermes WebUI privately"
```

## Task 7: Final Verification

**Files:**
- Verify: all changed files

- [ ] **Step 1: Run the focused Hermes and adjacent tests**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q tests/hermes tests/infra/test_lxc_resource_sizing.py tests/edge/test_caddyfile.py tests/edge/test_validate_playbook.py tests/repo/test_github_workflows.py --basetemp .pytest-basetemp\hermes-focused
```

Expected: PASS.

- [ ] **Step 2: Run the broader repo test suite**

Run:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-basetemp\hermes-all
```

Expected: PASS.

- [ ] **Step 3: Check the final diff for whitespace errors**

Run:

```powershell
git diff --check
```

Expected: no output.

- [ ] **Step 4: Check tracked worktree state**

Run:

```powershell
git status --short --untracked-files=no
```

Expected: no output after all task commits.
