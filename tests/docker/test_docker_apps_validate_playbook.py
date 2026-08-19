import os
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


def _posix_shell_command(script: str) -> list[str]:
    shell = shutil.which("sh")
    if shell and os.name != "nt":
        return [shell, script]

    git_shell = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
    )
    if not os.path.exists(git_shell):
        pytest.skip("POSIX shell is unavailable")
    drive, remainder = os.path.splitdrive(script)
    git_path = f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"
    return [git_shell, git_path]


def test_validation_checks_compose_dns_qbittorrent_and_routes():
    validate = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(encoding="utf-8")

    assert "Validate Docker Compose application host" in validate
    assert "docker compose --env-file .homelab/artifacts.env config --quiet" in validate
    assert "docker compose --env-file .homelab/artifacts.env ps --services --status running" in validate
    assert validate.count("docker compose --env-file .homelab/artifacts.env") >= 8
    assert "docker compose config" not in validate
    assert "dig +short @127.0.0.1" in validate
    assert "qbt.home.hchu.me" in validate
    assert "qbt-vpn.home.hchu.me" not in validate
    assert validate.count("metube.home.hchu.me") >= 2
    assert "t3code_hostname" in validate
    assert "copyparty.hchu.me" in validate
    assert "host_ip" in validate and "qbittorrent_ip" in validate
    assert 'test "$host_ip" = "$qbittorrent_ip"' in validate
    assert "vpn_ip" not in validate
    assert "docker port" in validate
    assert "current_network_interface" not in validate
    assert "Check AdGuard Safe Search is disabled" in validate
    assert "safe_search_enabled" in validate
    assert "Check Docker Engine reboot persistence" in validate
    assert "Run the active release smoke contract" in validate
    assert "./.homelab/smoke" in validate
    assert "stack-migration.json" not in validate
    assert "docker_apps_stack_migration_record" not in validate
    assert 'chdir: "{{ docker_apps_current_root }}/homelab"' in validate
    assert "docker_apps_compose_root }}/platform" not in validate
    assert "docker_apps_compose_root }}/media" not in validate
    assert "Validate the dedicated immutable OpenClaw runtime" in validate
    assert "Audit the active content-addressed release without another auth smoke" in validate
    assert "/usr/local/libexec/deploy_openclaw_release.py" in validate
    assert "openclaw_readiness_url" in validate
    assert "Check the OpenClaw CLI version" not in validate


def test_validation_proves_vuetorrent_assets_config_and_route():
    validation = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(
        encoding="utf-8"
    )

    assert "validate-vuetorrent.sh" in validation
    assert validation.index("validate-vuetorrent.sh") < validation.index(
        "- name: Check private Traefik routes"
    )
    assert "docker compose exec -T qbittorrent test -f" not in validation
    assert "qbt.home.hchu.me" in validation


def test_validation_has_no_live_legacy_cutover_state():
    validate = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(
        encoding="utf-8"
    )

    assert "stack-migration" not in validate
    assert "empty-host-reconstruction" not in validate
    assert "legacy cutover" not in validate.lower()


def test_vuetorrent_validator_reports_every_internal_diagnostic_before_failing():
    script = str(REPO_ROOT / "tests/docker/test_vuetorrent_validation.sh")
    env = os.environ.copy()
    if os.name == "nt":
        git_root = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"), "Git"
        )
        env["PATH"] = os.pathsep.join(
            [
                os.path.join(git_root, "usr", "bin"),
                os.path.join(git_root, "mingw64", "bin"),
                env["PATH"],
            ]
        )
    result = subprocess.run(
        _posix_shell_command(script),
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
