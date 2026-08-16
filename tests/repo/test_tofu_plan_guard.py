import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest

from tests.helpers import REPO_ROOT
from scripts.ci.homelab_topology import expected_lxc_count
GUARD = REPO_ROOT / "scripts" / "ci" / "check_tofu_plan_safe.py"
OPENCLAW_ADDRESS = (
    'module.target_lxc["openclaw"].'
    "proxmox_virtual_environment_container.this"
)


def exact_openclaw_create_change():
    """Representative OpenTofu JSON for the approved provider 0.110 create."""

    return {
        "actions": ["create"],
        "before": None,
        "after": {
            "vm_id": 118,
            "unprivileged": True,
            "started": True,
            "start_on_boot": True,
            "tags": ["homelab", "managed-by-opentofu", "role-openclaw"],
            "cpu": [{"cores": 4, "units": 1024}],
            "memory": [{"dedicated": 4096, "swap": 1024}],
            "initialization": [
                {
                    "hostname": "openclaw",
                    "ip_config": [
                        {
                            "ipv4": [
                                {
                                    "address": "192.168.0.5/24",
                                    "gateway": "192.168.0.1",
                                }
                            ]
                        }
                    ],
                }
            ],
            "network_interface": [
                {
                    "name": "veth0",
                    "bridge": "vmbr0",
                    "mac_address": "02:00:00:BA:EC:05",
                }
            ],
            "disk": [{"datastore_id": "local-lvm", "size": 32}],
            "startup": [{"order": 3, "up_delay": 15, "down_delay": 15}],
            "operating_system": [
                {
                    "type": "debian",
                    "template_file_id": (
                        "local:vztmpl/"
                        "debian-13-standard_13.1-2_amd64.tar.zst"
                    ),
                }
            ],
        },
        "after_unknown": {
            "cpu": [{}],
            "memory": [{}],
            "initialization": [{"ip_config": [{"ipv4": [{}]}]}],
            "network_interface": [{}],
            "disk": [{"path_in_datastore": True}],
            "startup": [{}],
            "operating_system": [{}],
        },
    }


def exact_openclaw_resource():
    return {
        "address": OPENCLAW_ADDRESS,
        "change": exact_openclaw_create_change(),
    }


def mutate_after(change, path, value):
    mutated = deepcopy(change)
    current = mutated["after"]
    for segment in path[:-1]:
        current = current[segment]
    current[path[-1]] = value
    return mutated


def run_guard(plan, *, allow_destroy=False, openclaw_stage_only=False):
    env = os.environ.copy()
    if allow_destroy:
        env["ALLOW_TOFU_DESTROY"] = "true"
    else:
        env.pop("ALLOW_TOFU_DESTROY", None)
    if openclaw_stage_only:
        env["OPENCLAW_NATIVE_STAGE_ONLY"] = "true"
    else:
        env.pop("OPENCLAW_NATIVE_STAGE_ONLY", None)

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






def test_tofu_plan_guard_allows_only_exact_additive_openclaw_target():
    result = run_guard(
        {
            "resource_changes": [exact_openclaw_resource()]
        }
    )

    assert result.returncode == 0
    assert "Approved additive dedicated OpenClaw target" in result.stdout
    assert "-> 118" in result.stdout


def test_tofu_plan_guard_rejects_empty_state_even_when_all_targets_are_approved():
    resources = [
        ("docker_apps", 110),
        ("tailnet", 111),
        ("openclaw", 118),
        ("ctf_executor", 119),
    ]
    plan_resources = []
    for name, vmid in resources:
        if name == "openclaw":
            plan_resources.append(exact_openclaw_resource())
        else:
            plan_resources.append(
                {
                    "address": (
                        f'module.target_lxc["{name}"].'
                        "proxmox_virtual_environment_container.this"
                    ),
                    "change": {
                        "actions": ["create"],
                        "after": {"vm_id": vmid},
                    },
                }
            )
    plan = {"resource_changes": plan_resources}

    rejected = run_guard(plan)
    assert rejected.returncode == 1
    assert "create-only" in rejected.stderr

    env = os.environ.copy()
    env["ALLOW_EMPTY_STATE_BOOTSTRAP"] = "true"
    allowed = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(plan),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert allowed.returncode == 0
    assert "ALLOW_EMPTY_STATE_BOOTSTRAP is set" in allowed.stderr


def test_tofu_plan_guard_rejects_mismatched_openclaw_vmid():
    change = exact_openclaw_create_change()
    change["after"]["vm_id"] = 119
    result = run_guard(
        {
            "resource_changes": [
                {
                    "address": OPENCLAW_ADDRESS,
                    "change": change,
                }
            ]
        }
    )

    assert result.returncode == 1
    assert "expected VMID 118" in result.stderr


def test_tofu_plan_guard_stage_mode_accepts_only_noop_and_exact_openclaw_create():
    result = run_guard(
        {
            "resource_changes": [
                {
                    "address": 'module.target_lxc["docker_apps"].proxmox_virtual_environment_container.this',
                    "change": {"actions": ["no-op"], "after": {"vm_id": 110}},
                },
                exact_openclaw_resource(),
            ]
        },
        openclaw_stage_only=True,
    )

    assert result.returncode == 0
    assert "additive OpenClaw stage only" in result.stdout


@pytest.mark.parametrize(
    ("field", "path", "unexpected"),
    [
        ("vm_id", ("vm_id",), 119),
        ("unprivileged", ("unprivileged",), False),
        ("started", ("started",), False),
        ("start_on_boot", ("start_on_boot",), False),
        ("tags_missing", ("tags",), ["homelab", "managed-by-opentofu"]),
        (
            "tags_extra",
            ("tags",),
            ["homelab", "managed-by-opentofu", "role-openclaw", "extra"],
        ),
        ("hostname", ("initialization", 0, "hostname"), "other"),
        (
            "ipv4_address",
            ("initialization", 0, "ip_config", 0, "ipv4", 0, "address"),
            "192.168.0.6/24",
        ),
        (
            "ipv4_gateway",
            ("initialization", 0, "ip_config", 0, "ipv4", 0, "gateway"),
            "192.168.0.254",
        ),
        ("network_name", ("network_interface", 0, "name"), "eth0"),
        (
            "mac_address",
            ("network_interface", 0, "mac_address"),
            "02:00:00:BA:EC:06",
        ),
        ("cores", ("cpu", 0, "cores"), 5),
        ("memory", ("memory", 0, "dedicated"), 8192),
        ("swap", ("memory", 0, "swap"), 0),
        ("disk_size", ("disk", 0, "size"), 64),
        ("startup_order", ("startup", 0, "order"), 4),
        ("os_type", ("operating_system", 0, "type"), "ubuntu"),
        (
            "os_template",
            ("operating_system", 0, "template_file_id"),
            "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst",
        ),
    ],
)
def test_tofu_plan_guard_stage_mode_rejects_each_contract_mutation(
    field, path, unexpected
):
    change = mutate_after(exact_openclaw_create_change(), path, unexpected)
    result = run_guard(
        {
            "resource_changes": [
                {"address": OPENCLAW_ADDRESS, "change": change}
            ]
        },
        openclaw_stage_only=True,
    )

    assert result.returncode == 1, field
    assert "invalid contract" in result.stderr


@pytest.mark.parametrize(
    ("malformation", "mutate"),
    [
        (
            "missing required value",
            lambda change: change["after"]["memory"][0].pop("swap"),
        ),
        (
            "multiple network blocks",
            lambda change: change["after"]["network_interface"].append(
                deepcopy(change["after"]["network_interface"][0])
            ),
        ),
        (
            "object instead of singleton block list",
            lambda change: change["after"].update(
                cpu=change["after"]["cpu"][0]
            ),
        ),
        (
            "wrong numeric type",
            lambda change: change["after"]["cpu"][0].update(cores="4"),
        ),
        (
            "unknown required value",
            lambda change: change["after_unknown"].update(vm_id=True),
        ),
        (
            "unknown nested required value",
            lambda change: change["after_unknown"]["disk"][0].update(size=True),
        ),
        (
            "unknown required tag element",
            lambda change: change["after_unknown"].update(
                tags=[False, True, False]
            ),
        ),
        (
            "malformed required tag metadata",
            lambda change: change["after_unknown"].update(tags={}),
        ),
        (
            "malformed unknown metadata",
            lambda change: change["after_unknown"]["startup"][0].update(
                order="unknown"
            ),
        ),
    ],
)
def test_tofu_plan_guard_stage_mode_fails_closed_on_malformed_or_unknown_contract(
    malformation, mutate
):
    change = exact_openclaw_create_change()
    mutate(change)
    result = run_guard(
        {
            "resource_changes": [
                {"address": OPENCLAW_ADDRESS, "change": change}
            ]
        },
        openclaw_stage_only=True,
    )

    assert result.returncode == 1, malformation
    assert "invalid contract" in result.stderr


def test_tofu_plan_guard_stage_mode_rejects_every_unrelated_change():
    for actions in (["update"], ["create"], ["delete", "create"]):
        result = run_guard(
            {
                "resource_changes": [
                    {
                        "address": 'module.target_lxc["docker_apps"].proxmox_virtual_environment_container.this',
                        "change": {"actions": actions, "after": {"vm_id": 110}},
                    }
                ]
            },
            allow_destroy=True,
            openclaw_stage_only=True,
        )

        assert result.returncode == 1
        assert "outside the additive OpenClaw stage" in result.stderr


def test_tofu_plan_guard_stage_mode_rejects_openclaw_update_or_wrong_vmid():
    update = exact_openclaw_create_change()
    update["actions"] = ["update"]
    wrong_vmid = mutate_after(exact_openclaw_create_change(), ("vm_id",), 119)
    for change in (update, wrong_vmid):
        result = run_guard(
            {
                "resource_changes": [
                    {"address": OPENCLAW_ADDRESS, "change": change}
                ]
            },
            openclaw_stage_only=True,
        )

        assert result.returncode == 1
        assert "outside the additive OpenClaw stage" in result.stderr


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
    workflow = (REPO_ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")
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
