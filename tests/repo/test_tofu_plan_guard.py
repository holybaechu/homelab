import json
import os
import subprocess
import sys

from tests.helpers import REPO_ROOT
GUARD = REPO_ROOT / "scripts" / "ci" / "check_tofu_plan_safe.py"
TOPOLOGY = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"


def expected_lxc_count():
    topology = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    return len(topology["all"]["children"]["debian"]["hosts"])


def run_guard(plan, *, allow_destroy=False):
    env = os.environ.copy()
    if allow_destroy:
        env["ALLOW_TOFU_DESTROY"] = "true"
    else:
        env.pop("ALLOW_TOFU_DESTROY", None)
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(plan),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_tofu_plan_guard_accepts_non_destructive_changes():
    result = run_guard(
        {
            "resource_changes": [
                {
                    "address": "module.lxc[\"dns\"]",
                    "change": {"actions": ["update"]},
                }
            ]
        }
    )

    assert result.returncode == 0
    assert "no unapproved destructive actions" in result.stdout


def test_tofu_plan_guard_rejects_delete_actions_by_default():
    result = run_guard(
        {
            "resource_changes": [
                {
                    "address": "module.lxc[\"dns\"]",
                    "change": {"actions": ["delete", "create"]},
                }
            ]
        }
    )

    assert result.returncode == 1
    assert "module.lxc" in result.stderr
    assert "ALLOW_TOFU_DESTROY" in result.stderr


def test_tofu_plan_guard_can_be_overridden_for_manual_destroy_workflows():
    result = run_guard(
        {
            "resource_changes": [
                {
                    "address": "module.lxc[\"dns\"]",
                    "change": {"actions": ["delete"]},
                }
            ]
        },
        allow_destroy=True,
    )

    assert result.returncode == 0
    assert "allowing destructive plan" in result.stderr


def test_tofu_plan_guard_rejects_create_only_lxc_plan_by_default():
    result = run_guard(
        {
            "resource_changes": [
                {
                    "address": f"module.target_lxc[\"svc{i}\"].proxmox_virtual_environment_container.this",
                    "change": {"actions": ["create"]},
                }
                for i in range(expected_lxc_count())
            ]
        }
    )

    assert result.returncode == 1
    assert "create-only" in result.stderr
    assert "ALLOW_EMPTY_STATE_BOOTSTRAP" in result.stderr


def test_tofu_plan_guard_allows_create_only_lxc_plan_for_explicit_bootstrap():
    env = os.environ.copy()
    env["ALLOW_EMPTY_STATE_BOOTSTRAP"] = "true"
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(
            {
                "resource_changes": [
                    {
                        "address": f"module.target_lxc[\"svc{i}\"].proxmox_virtual_environment_container.this",
                        "change": {"actions": ["create"]},
                    }
                    for i in range(expected_lxc_count())
                ]
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert "ALLOW_EMPTY_STATE_BOOTSTRAP is set" in result.stderr


def test_cd_manual_dispatch_guardedly_recovers_a_confirmed_stale_lock():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    plan_script = (REPO_ROOT / "scripts/ci/tofu-plan.sh").read_text(
        encoding="utf-8"
    )
    runbook = (REPO_ROOT / "docs/runbooks/github-actions.md").read_text(
        encoding="utf-8"
    )

    assert "tofu_force_unlock_id:" in workflow
    assert "TOFU_FORCE_UNLOCK_ID: ${{ inputs.tofu_force_unlock_id || '' }}" in workflow
    assert "grep -Eq" in plan_script
    assert "tofu force-unlock -force \"${TOFU_FORCE_UNLOCK_ID}\"" in plan_script
    assert plan_script.index("tofu init") < plan_script.index("tofu force-unlock")
    assert plan_script.index("tofu force-unlock") < plan_script.index("tofu state list")
    assert "confirmed stale lock" in runbook.lower()
    assert "tofu_force_unlock_id" in runbook
