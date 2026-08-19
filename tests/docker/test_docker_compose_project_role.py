from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import jinja2
import yaml

from tests.helpers import REPO_ROOT


ROLE_ROOT = REPO_ROOT / "infra/ansible/roles/docker_compose_project"
TASKS_PATH = ROLE_ROOT / "tasks/main.yml"
VARS_PATH = (
    REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
)
TEMPLATES = ROLE_ROOT / "templates"


def _tasks() -> list[dict]:
    parsed = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    return parsed


def _variables() -> dict:
    parsed = yaml.safe_load(VARS_PATH.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _template_keys(name: str) -> list[str]:
    return [
        line.split("=", 1)[0]
        for line in (TEMPLATES / name).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def test_inventory_declares_one_current_homelab_project():
    variables = _variables()

    assert variables["docker_apps_runtime_config_root"] == "/etc/homelab/runtime"
    assert variables["docker_apps_secret_root"] == "/etc/homelab/secrets"
    assert variables["docker_apps_current_root"] == "/opt/homelab/current"
    assert variables["docker_apps_deploy_state_root"] == "/opt/homelab/deploy-state"
    assert variables["docker_compose_projects"] == [
        {
            "name": "homelab",
            "src": "apps/compose/homelab",
            "dest": "{{ docker_apps_current_root }}/homelab",
            "env_template": "homelab.env.j2",
            "smoke_template": "homelab-smoke.sh.j2",
            "config_templates": [
                {
                    "src": "AdGuardHome.yaml.j2",
                    "dest": "adguard/AdGuardHome.yaml",
                    "mode": "0600",
                    "owner": "root",
                    "group": "root",
                    "force": False,
                },
                {
                    "src": "copyparty.conf.j2",
                    "dest": "copyparty.conf",
                    "mode": "0600",
                },
            ],
        }
    ]
    assert variables["docker_compose_secret_inputs"] == [
        {
            "src": "traefik.env.j2",
            "dest": "{{ docker_apps_secret_root }}/traefik.env",
        },
        {
            "src": "cloudflare-ddns.env.j2",
            "dest": "{{ docker_apps_secret_root }}/cloudflare-ddns.env",
        },
    ]


def test_homelab_environment_is_nonsecret_and_service_secrets_are_minimal():
    assert _template_keys("homelab.env.j2") == [
        "PUID",
        "PGID",
        "TZ",
        "QBT_DIRECT_PEER_PORT",
        "DOMAINS",
        "IP6_PROVIDER",
        "PROXIED",
    ]
    homelab = (TEMPLATES / "homelab.env.j2").read_text(encoding="utf-8")
    assert "TOKEN" not in homelab
    assert "PASSWORD" not in homelab
    assert _template_keys("traefik.env.j2") == ["CF_DNS_API_TOKEN"]
    assert _template_keys("cloudflare-ddns.env.j2") == [
        "CLOUDFLARE_API_TOKEN"
    ]
    assert not (TEMPLATES / "platform.env.j2").exists()
    assert not (TEMPLATES / "media.env.j2").exists()
    assert not (TEMPLATES / "t3code.env.j2").exists()


def test_role_only_prepares_inputs_and_never_controls_compose_lifecycle():
    text = TASKS_PATH.read_text(encoding="utf-8")
    lowered = text.lower()

    for command in (
        "docker compose up",
        "docker compose down",
        "docker compose pull",
        "docker compose stop",
        "--force-recreate",
        "--build",
    ):
        assert command not in lowered
    assert "docker_compose_force_recreate" not in text
    assert "Render Compose project environment files" not in text
    assert "Copy tracked Compose runtime files" not in text
    assert "Read effective qBittorrent Web UI preferences" not in text
    assert "Verify AdGuard runtime Safe Search is disabled" not in text


def test_role_materializes_exact_private_root_owned_input_contract():
    tasks = _tasks()
    text = TASKS_PATH.read_text(encoding="utf-8")

    manifest = "\n".join(
        yaml.safe_dump(task)
        for task in tasks
        if "docker_compose_managed_inputs" in yaml.safe_dump(task)
    )
    assert "item.env_template" in manifest
    assert "item.1.src" in manifest
    assert "'/files/' + item.1.dest" in manifest
    assert "docker_compose_secret_inputs" in manifest
    assert "item.smoke_template" in manifest
    assert "'/smoke'" in manifest
    assert "'mode': '0700'" in manifest

    directory = next(
        task
        for task in tasks
        if task["name"] == "Create private direct-deployment input directories"
    )["ansible.builtin.file"]
    assert directory == {
        "path": "{{ item }}",
        "state": "directory",
        "owner": "root",
        "group": "root",
        "mode": "0700",
        "follow": False,
    }

    render_task = next(
        task
        for task in tasks
        if task["name"] == "Render private direct-deployment input files"
    )
    render = render_task["ansible.builtin.template"]
    assert render["owner"] == "root"
    assert render["group"] == "root"
    assert render["mode"] == "{{ item.mode | default('0600') }}"
    assert render["follow"] is False
    assert render_task["no_log"] is True

    metadata = next(
        task
        for task in tasks
        if task["name"] == "Verify private direct-deployment input metadata"
    )
    conditions = "\n".join(metadata["ansible.builtin.assert"]["that"])
    assert "stat.isreg" in conditions
    assert "stat.islnk" in conditions
    assert "stat.uid == 0" in conditions
    assert "stat.gid == 0" in conditions
    assert "stat.mode == (item.item.mode | default('0600'))" in conditions

    assert text.count("follow: false") >= 5
    assert "Require unique managed input destinations" in text


def test_runtime_overlays_use_the_requested_homelab_paths():
    variables = _variables()
    project = variables["docker_compose_projects"][0]
    root = variables["docker_apps_runtime_config_root"]

    assert f"{root}/{project['name']}/.env" == "/etc/homelab/runtime/homelab/.env"
    assert f"{root}/{project['name']}/smoke" == "/etc/homelab/runtime/homelab/smoke"
    assert [
        f"{root}/{project['name']}/files/{item['dest']}"
        for item in project["config_templates"]
    ] == [
        "/etc/homelab/runtime/homelab/files/adguard/AdGuardHome.yaml",
        "/etc/homelab/runtime/homelab/files/copyparty.conf",
    ]


def test_mandatory_smoke_contract_covers_health_dns_and_shared_ingress():
    smoke = (TEMPLATES / "homelab-smoke.sh.j2").read_text(encoding="utf-8")

    assert "docker compose" in smoke
    assert "--status running" in smoke
    assert "docker inspect" in smoke
    assert "RestartCount" in smoke
    assert "dig +short" in smoke
    assert "@127.0.0.1" in smoke
    assert "--resolve" in smoke
    for service in (
        "traefik",
        "adguard",
        "cloudflare-ddns",
        "qbittorrent",
        "copyparty",
        "metube",
        "t3code",
    ):
        assert service in smoke
    for forbidden in ("TOKEN", "PASSWORD", "SECRET"):
        assert forbidden not in smoke


def test_mandatory_smoke_contract_renders_and_has_valid_posix_shell_syntax():
    source = (TEMPLATES / "homelab-smoke.sh.j2").read_text(encoding="utf-8")
    rendered = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    ).from_string(source).render(
        docker_apps_ip="192.0.2.10",
        t3code_hostname="code.home.hchu.me",
        traefik_domain="hchu.me",
        traefik_private_domain="home.hchu.me",
    )

    shell = shutil.which("sh")
    if shell is None and os.name == "nt":
        candidate = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        ) / "Git/bin/sh.exe"
        if candidate.exists():
            shell = str(candidate)
    assert shell is not None, "a POSIX shell is required to validate the smoke contract"
    result = subprocess.run(
        [shell, "-n"],
        input=rendered,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_superseded_runtime_inputs_are_removed_without_touching_rollback_releases():
    tasks = _tasks()
    text = TASKS_PATH.read_text(encoding="utf-8")

    removal = next(
        task
        for task in tasks
        if task["name"] == "Remove superseded split-project runtime inputs"
    )
    assert removal["loop"] == ["platform", "media", "code"]
    assert removal["ansible.builtin.file"] == {
        "path": "{{ docker_apps_runtime_config_root }}/{{ item }}",
        "state": "absent",
        "follow": False,
    }
    guard = next(
        task
        for task in tasks
        if task["name"] == "Reject redirected superseded runtime directories"
    )
    assert "stat.islnk" in yaml.safe_dump(guard)
    assert "/opt/homelab/releases" not in text
    assert "state: absent" in text


def test_adguard_is_root_owned_bootstrap_input_and_qbittorrent_state_is_preserved():
    tasks = _tasks()
    text = TASKS_PATH.read_text(encoding="utf-8")

    adguard_read = next(
        task
        for task in tasks
        if task["name"] == "Read existing AdGuard admin password hash"
    )
    assert (
        "{{ docker_apps_runtime_config_root }}/homelab/files/adguard/AdGuardHome.yaml"
        in adguard_read["ansible.builtin.command"]["argv"]
    )
    assert adguard_read["no_log"] is True

    bootstrap = next(
        task
        for task in tasks
        if task["name"]
        == "Bootstrap qBittorrent configuration without replacing application state"
    )
    template = bootstrap["ansible.builtin.template"]
    assert template["src"] == "qBittorrent.conf.j2"
    assert template["force"] is False
    assert template["mode"] == "0600"
    assert bootstrap["no_log"] is True
    assert 'loop: "{{ qbittorrent_instances }}"' in text
    assert "community.general.ini_file" not in text


def test_role_keeps_storage_and_external_network_preparation_without_starting_apps():
    tasks = _tasks()
    text = TASKS_PATH.read_text(encoding="utf-8")

    assert any(
        task["name"] == "Require the Proxmox homelab bind mount before writing application data"
        for task in tasks
    )
    assert any(task["name"] == "Create shared Traefik proxy network" for task in tasks)
    for path in (
        "/srv/homelab/docker-apps/qbittorrent/qBittorrent",
        "/srv/homelab/docker-apps/copyparty",
        "/srv/homelab/downloads/complete",
        "/srv/homelab/copyparty/public",
    ):
        assert path in text
