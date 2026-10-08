"""Protect production ordering, trust, and credentials in the three workflows."""

import re
import shlex
import json
import sys
from unittest.mock import patch

import pytest
import yaml

from tests.helpers import REPO_ROOT


WORKFLOWS = REPO_ROOT / ".github/workflows"


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def commands(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def step_with(steps, fragment):
    matches = [(index, step) for index, step in enumerate(steps) if fragment in step.get("run", "")]
    assert len(matches) == 1, fragment
    return matches[0]


def test_headscale_cutover_preserves_the_hosted_bootstrap_path():
    for name in ("apps.yml", "infra.yml"):
        job = next(iter(workflow(name)["jobs"].values()))
        connect = next(step for step in job["steps"] if step.get("uses", "").startswith("tailscale/"))
        inputs = connect["with"]
        assert inputs["authkey"] == "${{ secrets.HEADSCALE_CI_AUTH_KEY }}"
        assert "secrets.HEADSCALE_CI_AUTH_KEY == ''" in inputs["oauth-client-id"]
        assert "--login-server=https://headscale.home.hchu.me" in inputs["args"]
        assert "--accept-routes=true" in inputs["args"]
        assert "--accept-dns=false" in inputs["args"]
    _, gateway = step_with(workflow("infra.yml")["jobs"]["reconcile"]["steps"], '"component": "tailnet"')
    assert gateway["env"]["HEADSCALE_GATEWAY_AUTH_KEY"] == "${{ secrets.HEADSCALE_GATEWAY_AUTH_KEY }}"
    assert '"version": 2' in gateway["run"]


@pytest.mark.parametrize("operator,expected", (
    ("", ["ssh-ed25519 QUJD"]),
    ("ssh-ed25519 REVG operator@homelab", ["ssh-ed25519 QUJD", "ssh-ed25519 REVG"]),
    ("ssh-ed25519 QUJD same-key", ["ssh-ed25519 QUJD"]),
))
def test_pve_bundle_keeps_deployment_access_when_adding_an_operator(tmp_path, operator, expected):
    steps = workflow("infra.yml")["jobs"]["reconcile"]["steps"]
    _, materialize = step_with(steps, '"component": "pve"')
    assert materialize["env"]["OPERATOR_SSH_PUBLIC_KEY"] == "${{ vars.OPERATOR_SSH_PUBLIC_KEY }}"
    program = materialize["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    destination = tmp_path / "pve.json"
    with patch.dict("os.environ", {"OPERATOR_SSH_PUBLIC_KEY": operator}), \
         patch.object(sys, "argv", ["-", str(destination)]), \
         patch("subprocess.check_output", return_value="ssh-ed25519 QUJD deploy-comment\n"):
        exec(compile(program, "pve-bundle-workflow", "exec"), {})
    assert json.loads(destination.read_text())["values"]["deploy_ssh_public_keys"] == expected


@pytest.mark.parametrize("operator", (
    "from=192.0.2.1 ssh-ed25519 REVG",
    "ssh-ed25519 REVG\nssh-ed25519 QUJD",
    "ssh-ed25519 not-base64!",
))
def test_invalid_operator_key_stops_before_writing_a_pve_bundle(tmp_path, operator):
    _, materialize = step_with(workflow("infra.yml")["jobs"]["reconcile"]["steps"], '"component": "pve"')
    program = materialize["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    destination = tmp_path / "pve.json"
    with patch.dict("os.environ", {"OPERATOR_SSH_PUBLIC_KEY": operator}), \
         patch.object(sys, "argv", ["-", str(destination)]), \
         patch("subprocess.check_output", return_value="ssh-ed25519 QUJD\n"):
        with pytest.raises(ValueError, match="Operator SSH public key"):
            exec(compile(program, "pve-bundle-workflow", "exec"), {})
    assert not destination.exists()


def test_pve_and_authentik_render_the_same_private_oidc_credential(tmp_path):
    from tests.docker.test_prepare_release import valid_bundle, stage_and_bundle, run_prepare
    apps = valid_bundle()
    stage, source = stage_and_bundle(tmp_path / 'apps', apps)
    assert run_prepare(stage, source).returncode == 0
    _, materialize = step_with(workflow('infra.yml')['jobs']['reconcile']['steps'], '"component": "pve"')
    program = materialize['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    destination = tmp_path / 'pve.json'
    with patch.dict('os.environ', {'OPERATOR_SSH_PUBLIC_KEY': '', 'PVE_IDENTITY_ONLY': 'true', 'APPS_SECRET_BUNDLE': json.dumps(apps)}), \
         patch.object(sys, 'argv', ['-', str(destination)]), \
         patch('subprocess.check_output', return_value='ssh-ed25519 QUJD\n'):
        exec(compile(program, 'pve-bundle-workflow', 'exec'), {})
    pve = json.loads(destination.read_text())
    assert pve['version'] == 2
    from scripts.ci.verify_pve_access_bundle import require_identity_membership
    require_identity_membership(destination, 'ssh-ed25519 QUJD')
    secret = pve['values']['proxmox_oidc_client_secret']
    env = dict(line.split('=', 1) for line in (stage / '.secrets/authentik.env').read_text().splitlines())
    assert secret == env['PROXMOX_OIDC_CLIENT_SECRET']
    assert secret != apps['authentik']['secret_key'] and secret != apps['headscale']['oidc_client_secret']
