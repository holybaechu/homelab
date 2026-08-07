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


def test_validation_checks_compose_dns_qbittorrent_routes_and_arcane():
    validate = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(encoding="utf-8")

    assert "Validate Docker Compose application host" in validate
    assert "docker compose config --quiet" in validate
    assert "docker compose ps --services --status running" in validate
    assert "Validate the Arcane control project" in validate
    assert "Check Arcane API health" in validate
    assert "arcane.db" in validate
    assert "docker-socket-proxy" in validate
    assert "ADMIN_STATIC_API_KEY" in validate
    assert "ADMIN_STATIC_API_KEY_FILE" in validate
    assert "arcane.home.hchu.me" in validate or "arcane_hostname" in validate
    assert "dig +short @127.0.0.1" in validate
    assert "qbt.home.hchu.me" in validate
    assert validate.count("public.qbt.home.hchu.me") == 2
    assert "qbt-vpn.home.hchu.me" not in validate
    assert validate.count("metube.home.hchu.me") >= 2
    assert "copyparty.hchu.me" in validate
    assert "host_ip" in validate and "qbittorrent_ip" in validate
    assert 'test "$host_ip" = "$qbittorrent_ip"' in validate
    assert "vpn_ip" not in validate
    assert "docker port" in validate
    assert "current_network_interface" not in validate
    assert "Check AdGuard Safe Search is disabled" in validate
    assert "safe_search_enabled" in validate
    assert "Assert retired Compose service containers are absent" in validate
    assert "Assert retired Docker volumes are absent" in validate
    assert "Assert retired Docker networks are absent" in validate
    assert "Assert retired local Docker images are absent" in validate
    assert "hermes status" not in validate


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
    assert "public.qbt.home.hchu.me" in validation
    assert "--insecure" in validation
    assert "retired_public_qbittorrent_route.stdout == '404'" in validation


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
