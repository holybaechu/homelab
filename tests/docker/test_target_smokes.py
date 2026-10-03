from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from tests.helpers import REPO_ROOT, posix_shell, shell_path, shell_environment_path, write_tool


APPS_PACKAGE = REPO_ROOT / "apps/compose/homelab"
TOPOLOGY = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"


def fake_app_environment(tmp_path: Path, *, ingress_failure: bool = False) -> dict[str, str]:
    tools = tmp_path / "tools"
    tools.mkdir()
    model = tmp_path / "compose.json"
    model.write_text(
        json.dumps(
            {
                "services": {
                    "one": {
                        "labels": {
                            "homelab.smoke.url": "https://one.home.example/"
                        }
                    },
                    "two": {
                        "labels": [
                            "homelab.smoke.url=https://two.home.example/"
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    write_tool(tools / "id", "[ \"${1:-}\" = -u ] && printf '0\\n'\n")
    write_tool(
        tools / "dig",
        r'''
case "$*" in
  *qbt.home.hchu.me*) printf '192.168.0.3\n' ;;
  *example.com*) printf '1.1.1.1\n' ;;
  *) exit 9 ;;
esac
''',
    )
    write_tool(
        tools / "curl",
        r'''
case "$*" in
  *api.ipify.org*) printf '203.0.113.9\n' ;;
  *one.home.example*|*two.home.example*)
    [ "${FAKE_INGRESS_FAILURE:-0}" = 0 ] || exit 22
    ;;
  *) exit 9 ;;
esac
''',
    )
    write_tool(tools / "sleep", ":\n")
    write_tool(
        tools / "docker",
        r'''
case "$*" in
  *"config --format json"*) cat "$FAKE_COMPOSE_MODEL" ;;
  *"exec -T qbittorrent sh -c"*"api.ipify.org"*) printf '203.0.113.9\n' ;;
  *"exec -T qbittorrent sh -c"*"app/preferences"*) printf '{"listen_port":35435}\n' ;;
  *"ps -q qbittorrent"*) printf 'container-id\n' ;;
  *"port container-id 35435/tcp"*|*"port container-id 35435/udp"*)
    printf '0.0.0.0:35435\n'
    ;;
  *"exec -T qbittorrent test -f /vuetorrent/public/index.html"*)
    [ "${FAKE_VUETORRENT_FAILURE:-}" != assets ] ;;
  *"Connection\\Interface=tun0"*)
    [ "${FAKE_VUETORRENT_FAILURE:-}" = tun0 ] ;;
  *"exec -T qbittorrent grep -Fx --"*)
    [ "${FAKE_VUETORRENT_FAILURE:-}" != config ] ;;
  *"exec -T qbittorrent printenv DOCKER_MODS"*)
    if [ "${FAKE_VUETORRENT_FAILURE:-}" = environment ]; then
      printf 'container-secret-must-stay-private\n' >&2
      exit 77
    fi
    printf '%s\n' "$FAKE_DOCKER_MOD_REF"
    ;;
  *) printf 'unexpected fake docker command: %s\n' "$*" >&2; exit 97 ;;
esac
''',
    )
    write_tool(
        tools / "python3",
        f'exec "{shell_path(Path(sys.executable))}" "$@"\n',
    )

    env = os.environ.copy()
    env["FAKE_TOOLS"] = shell_path(tools)
    env["FAKE_COMPOSE_MODEL"] = shell_environment_path(model)
    env["FAKE_INGRESS_FAILURE"] = "1" if ingress_failure else "0"
    compose = yaml.safe_load((APPS_PACKAGE / "compose.yml").read_text(encoding="utf-8"))
    env["FAKE_DOCKER_MOD_REF"] = compose["services"]["qbittorrent"]["environment"][
        "DOCKER_MODS"
    ]
    return env


def app_stage(tmp_path: Path) -> Path:
    stage = tmp_path / "package"
    shutil.copytree(APPS_PACKAGE, stage)
    shutil.copy2(TOPOLOGY, stage / "topology.json")
    generated = stage / "generated/adguard/AdGuardHome.yaml"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        "filtering:\n  safe_search:\n    enabled: false\n",
        encoding="utf-8",
    )
    return stage


def run_app_smoke(stage: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            posix_shell(),
            "-c",
            'PATH="$1:$PATH"; export PATH; shift; exec sh "$@"',
            "smoke-harness",
            env["FAKE_TOOLS"],
            "./smoke.sh",
        ],
        cwd=stage,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def test_apps_smoke_executes_every_semantic_probe(tmp_path: Path) -> None:
    stage = app_stage(tmp_path)
    result = run_app_smoke(stage, fake_app_environment(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "homelab smoke passed" in result.stdout


def test_apps_smoke_fails_when_a_declared_ingress_is_unreachable(tmp_path: Path) -> None:
    stage = app_stage(tmp_path)
    result = run_app_smoke(stage, fake_app_environment(tmp_path, ingress_failure=True))

    assert result.returncode == 1
    assert "shared ingress route failed for one.home.example" in result.stderr


@pytest.mark.parametrize("failure,message", (
    ("assets", "VueTorrent assets are unavailable"),
    ("config", "qBittorrent VueTorrent configuration is incorrect"),
    ("tun0", "qBittorrent is unexpectedly bound to tun0"),
    ("environment", "VueTorrent mod environment is unavailable"),
    ("unpinned", "VueTorrent mod must use an official version and exact digest"),
))
def test_apps_smoke_rejects_a_broken_vuetorrent_without_printing_container_values(
    tmp_path: Path, failure: str, message: str
) -> None:
    stage = app_stage(tmp_path)
    env = fake_app_environment(tmp_path)
    env["FAKE_VUETORRENT_FAILURE"] = failure
    if failure == "unpinned":
        env["FAKE_DOCKER_MOD_REF"] = "container-secret-must-stay-private"

    result = run_app_smoke(stage, env)

    assert result.returncode == 1
    assert message in result.stderr
    assert "container-secret-must-stay-private" not in result.stdout + result.stderr
