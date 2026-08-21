from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Iterator

import pytest
import yaml

from tests.helpers import REPO_ROOT


APPS_PACKAGE = REPO_ROOT / "apps/compose/homelab"
TOPOLOGY = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"
OPENCLAW_SMOKE = REPO_ROOT / "infra/openclaw/runtime/smoke.sh"


def posix_shell() -> str:
    shell = shutil.which("sh")
    if shell is not None:
        return shell
    for candidate in (
        Path("C:/Program Files/Git/bin/sh.exe"),
        Path("C:/Program Files/Git/usr/bin/sh.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    pytest.skip("POSIX sh is unavailable")


def shell_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    drive, remainder = os.path.splitdrive(str(resolved))
    return f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"


def shell_environment_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def write_tool(path: Path, source: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + source, encoding="utf-8", newline="\n")
    path.chmod(0o755)


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
  *"exec -T qbittorrent test -f /vuetorrent/public/index.html"*) exit 0 ;;
  *"Connection\\Interface=tun0"*) exit 1 ;;
  *"exec -T qbittorrent grep -Fx --"*) exit 0 ;;
  *"exec -T qbittorrent printenv DOCKER_MODS"*)
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
    assert "effective DOCKER_MODS=" in result.stdout


def test_apps_smoke_fails_when_a_declared_ingress_is_unreachable(tmp_path: Path) -> None:
    stage = app_stage(tmp_path)
    result = run_app_smoke(stage, fake_app_environment(tmp_path, ingress_failure=True))

    assert result.returncode == 1
    assert "shared ingress route failed for one.home.example" in result.stderr


@contextmanager
def openclaw_gateway(
    token: str,
    *,
    accept_any_control_token: bool = False,
) -> Iterator[list[tuple[str, str | None]]]:
    requests: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            authorization = self.headers.get("Authorization")
            requests.append((self.path, authorization))
            if self.path == "/readyz":
                status = 204
            elif self.path == "/control-ui-config.json" and (
                accept_any_control_token or authorization == f"Bearer {token}"
            ):
                status = 200
            else:
                status = 401
            self.send_response(status)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 18789), Handler)
    except OSError as error:
        pytest.skip(f"OpenClaw smoke port is unavailable: {error}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="production secret modes require POSIX")
def test_openclaw_smoke_checks_readiness_and_authenticated_control_surface(
    tmp_path: Path,
) -> None:
    token = "a" * 64
    secret_root = tmp_path / "secrets"
    secret_root.mkdir(mode=0o700)
    token_path = secret_root / "gateway_token"
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    env = os.environ.copy()
    env["OPENCLAW_SECRET_ROOT"] = str(secret_root)

    with openclaw_gateway(token) as requests:
        result = subprocess.run(
            [posix_shell(), str(OPENCLAW_SMOKE)],
            cwd=OPENCLAW_SMOKE.parent,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert requests == [
        ("/readyz", None),
        ("/control-ui-config.json", None),
        ("/control-ui-config.json", f"Bearer {'0' * 64}"),
        ("/control-ui-config.json", f"Bearer {token}"),
    ]
    assert "authenticated smoke passed" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="production secret modes require POSIX")
def test_openclaw_smoke_rejects_a_control_surface_without_enforced_authentication(
    tmp_path: Path,
) -> None:
    token = "a" * 64
    secret_root = tmp_path / "secrets"
    secret_root.mkdir(mode=0o700)
    token_path = secret_root / "gateway_token"
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    env = os.environ.copy()
    env["OPENCLAW_SECRET_ROOT"] = str(secret_root)

    with openclaw_gateway(token, accept_any_control_token=True) as requests:
        result = subprocess.run(
            [posix_shell(), str(OPENCLAW_SMOKE)],
            cwd=OPENCLAW_SMOKE.parent,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    assert result.returncode != 0
    assert requests == [
        ("/readyz", None),
        ("/control-ui-config.json", None),
    ]
    assert "expected 401 or 403" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="production secret modes require POSIX")
def test_openclaw_smoke_rejects_an_invalid_gateway_token_before_network_access(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    secret_root.mkdir(mode=0o700)
    token_path = secret_root / "gateway_token"
    token_path.write_text("not-a-token\n", encoding="utf-8")
    token_path.chmod(0o600)
    env = os.environ.copy()
    env["OPENCLAW_SECRET_ROOT"] = str(secret_root)

    result = subprocess.run(
        [posix_shell(), str(OPENCLAW_SMOKE)],
        cwd=OPENCLAW_SMOKE.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Gateway token is not exact lowercase 64-hex" in result.stderr
