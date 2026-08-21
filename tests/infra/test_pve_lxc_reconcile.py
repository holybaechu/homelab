import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tests.helpers import REPO_ROOT


SCRIPT = (
    REPO_ROOT
    / "infra/ansible/roles/pve_lxc_reconcile/files/pve_lxc_reconcile.py"
)
TOPOLOGY = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"


def load_module():
    spec = importlib.util.spec_from_file_location("pve_lxc_reconcile", SCRIPT)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


reconcile = load_module()


def topology_data():
    return reconcile.load_topology(TOPOLOGY)


def exact_config_for(all_vars: dict, host: dict, **overrides: str) -> str:
    desired = reconcile.desired_config(all_vars, host)
    values = {
        "hostname": desired["hostname"],
        "ostype": desired["ostype"],
        "unprivileged": desired["unprivileged"],
        "cores": desired["cores"],
        "memory": desired["memory"],
        "swap": desired["swap"],
        "onboot": desired["onboot"],
        "startup": "down=15,order=%s,up=15" % host["startup_order"],
        "description": desired["description"],
        "tags": ";".join(reversed(desired["tags"])),
        "nameserver": desired["nameserver"],
        "searchdomain": desired["searchdomain"],
        "net0": reconcile._format_options(desired["net0"]),
        "features": reconcile._format_options(desired["features"]),
        "rootfs": (
            f"{desired['rootfs_datastore']}:vm-{host['vmid']}-disk-0,"
            f"size={desired['rootfs_size_gb']}G"
        ),
    }
    values.update(desired["devices"])
    values.update(desired["mounts"])
    values.update(overrides)
    return "".join(f"{key}: {value}\n" for key, value in values.items())


def exact_config(name: str, **overrides: str) -> str:
    all_vars, hosts = topology_data()
    return exact_config_for(all_vars, hosts[name], **overrides)


class FakeRunner:
    def __init__(self, configs=None, running=()):
        _, hosts = topology_data()
        self.configs = {
            int(host["vmid"]): exact_config(name) for name, host in hosts.items()
        }
        if configs:
            self.configs.update(configs)
        self.running_vmids = set(running)
        self.calls = []

    def config(self, vmid):
        return self.configs[vmid]

    def running(self, vmid):
        return vmid in self.running_vmids

    def run(self, argv, *, check=True):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")


def invoke(
    tmp_path: Path,
    runner: FakeRunner,
    command: str,
    *extra: str,
    topology: Path = TOPOLOGY,
    mount_inspector=lambda _host: (),
) -> int:
    return reconcile.main(
        [
            command,
            "--topology",
            str(topology),
            "--export-dir",
            str(tmp_path / "exports"),
            *extra,
        ],
        runner=runner,
        mount_inspector=mount_inspector,
    )


def test_exact_live_configuration_is_idempotent(tmp_path, capsys):
    runner = FakeRunner()

    assert invoke(tmp_path, runner, "apply") == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["changed"] is False
    assert payload["exports"] == []
    assert runner.calls == []
    assert not (tmp_path / "exports").exists()


def test_plan_and_audit_report_safe_field_diff_without_mutation(tmp_path, capsys):
    runner = FakeRunner({111: exact_config("tailnet", cores="2")})

    assert invoke(tmp_path, runner, "plan") == 0
    plan = json.loads(capsys.readouterr().out)
    tailnet = next(item for item in plan["containers"] if item["vmid"] == 111)
    assert tailnet["changes"] == [
        {
            "after": "1",
            "before": "2",
            "field": "cores",
            "operation": "set",
            "risk": "safe",
        }
    ]
    assert runner.calls == []
    assert not (tmp_path / "exports").exists()

    assert invoke(tmp_path, runner, "audit") == 1
    assert json.loads(capsys.readouterr().out)["changed"] is True
    assert runner.calls == []


def test_apply_exports_pct_config_then_reconciles_a_safe_field(tmp_path, capsys):
    before = exact_config("tailnet", cores="2")
    runner = FakeRunner({111: before})

    assert invoke(tmp_path, runner, "apply") == 0
    payload = json.loads(capsys.readouterr().out)

    assert runner.calls == [["pct", "set", "111", "--cores", "1"]]
    assert len(payload["exports"]) == 1
    exported = Path(payload["exports"][0])
    assert exported.read_text(encoding="utf-8") == before
    assert exported.name.endswith("-111-tailnet.conf")
    if os.name != "nt":
        assert exported.stat().st_mode & 0o777 == 0o600


def test_unconfirmed_destructive_drift_rejects_the_whole_plan(tmp_path, capsys):
    runner = FakeRunner(
        {
            111: exact_config("tailnet", dev1="/dev/obsolete"),
            110: exact_config("docker_apps", memory="4096"),
        }
    )

    assert invoke(tmp_path, runner, "apply") == 2
    error = capsys.readouterr().err

    assert "--allow-destructive 111" in error
    assert runner.calls == []
    assert not (tmp_path / "exports").exists()


def test_apply_preflight_reports_the_complete_plan_without_export_or_mutation(
    tmp_path, capsys
):
    runner = FakeRunner({110: exact_config("docker_apps", memory="4096")})

    assert invoke(tmp_path, runner, "apply", "--preflight-only") == 0
    payload = json.loads(capsys.readouterr().out)

    apps = next(item for item in payload["containers"] if item["vmid"] == 110)
    assert payload["changed"] is True
    assert apps["changes"] == [
        {
            "after": "8192",
            "before": "4096",
            "field": "memory",
            "operation": "set",
            "risk": "safe",
        }
    ]
    assert payload["exports"] == []
    assert runner.calls == []
    assert not (tmp_path / "exports").exists()


def test_exact_vmid_confirmation_allows_destructive_reconcile(tmp_path, capsys):
    runner = FakeRunner(
        {111: exact_config("tailnet", dev1="/dev/obsolete")}, running=(111,)
    )

    assert invoke(tmp_path, runner, "apply", "--allow-destructive", "111") == 0
    capsys.readouterr()

    assert runner.calls == [
        ["pct", "shutdown", "111", "--timeout", "60"],
        ["pct", "set", "111", "--delete", "dev1"],
        ["pct", "start", "111"],
    ]


def test_active_control_path_rejects_connectivity_changes_on_the_remote_lane(
    tmp_path, capsys
):
    runner = FakeRunner(
        {111: exact_config("tailnet", hostname="old-tailnet")}, running=(111,)
    )

    assert invoke(
        tmp_path,
        runner,
        "apply",
        "--protect-control-vmid",
        "111",
    ) == 2

    assert "requires an out-of-band apply" in capsys.readouterr().err
    assert runner.calls == []
    assert not (tmp_path / "exports").exists()


def test_failed_restart_field_reconcile_restores_a_previously_running_lxc(
    tmp_path, capsys
):
    class FailingRunner(FakeRunner):
        def run(self, argv, *, check=True):
            result = super().run(argv, check=check)
            if list(argv)[:3] == ["pct", "set", "111"]:
                raise reconcile.ReconcileError("injected pct set failure")
            return result

    runner = FailingRunner(
        {111: exact_config("tailnet", hostname="old-tailnet")}, running=(111,)
    )

    assert invoke(tmp_path, runner, "apply") == 2
    assert "injected pct set failure" in capsys.readouterr().err
    assert runner.calls == [
        ["pct", "shutdown", "111", "--timeout", "60"],
        ["pct", "set", "111", "--hostname", "tailnet"],
        ["pct", "start", "111"],
    ]


def test_replacement_drift_requires_a_separate_exact_confirmation(tmp_path, capsys):
    runner = FakeRunner({111: exact_config("tailnet", ostype="ubuntu")})

    assert invoke(tmp_path, runner, "apply", "--allow-destructive", "111") == 2
    assert "--allow-replacement 111" in capsys.readouterr().err
    assert runner.calls == []


def test_replacement_confirmation_recreates_only_the_named_vmid(tmp_path, capsys):
    runner = FakeRunner({111: exact_config("tailnet", ostype="ubuntu")})

    assert invoke(tmp_path, runner, "apply", "--allow-replacement", "111") == 0
    capsys.readouterr()

    assert runner.calls[0] == ["pct", "destroy", "111", "--purge", "1"]
    assert runner.calls[1][:4] == ["pct", "create", "111", "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"]
    assert runner.calls[-1] == ["pct", "start", "111"]
    assert all("110" not in call and "118" not in call for call in runner.calls)


def test_missing_container_is_created_and_absence_is_exported(tmp_path, capsys):
    runner = FakeRunner({111: None})

    assert invoke(tmp_path, runner, "apply") == 0
    payload = json.loads(capsys.readouterr().out)

    assert runner.calls[0][:4] == ["pct", "create", "111", "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"]
    assert runner.calls[-1] == ["pct", "start", "111"]
    assert Path(payload["exports"][0]).read_text(encoding="utf-8") == "ABSENT\n"


@pytest.mark.skipif(os.name == "nt", reason="PVE bind sources use POSIX absolute paths")
def test_missing_bind_sources_are_planned_and_reconciled_without_pct_mutation(
    tmp_path, capsys
):
    document = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    hosts = document["all"]["children"]["debian"]["hosts"]
    owner = os.getuid() if hasattr(os, "getuid") else 0
    group = os.getgid() if hasattr(os, "getgid") else 0
    expected_paths: dict[int, Path] = {}
    for index, name in enumerate(("docker_apps", "openclaw"), start=1):
        host = hosts[name]
        mount = host["lxc_mounts"]["mp0"]
        source = tmp_path / f"mount-source-{index}"
        mount.update(
            source=str(source),
            source_owner=owner,
            source_group=group,
            source_mode="0700",
        )
        expected_paths[host["vmid"]] = source
    custom_topology = tmp_path / "topology.json"
    custom_topology.write_text(json.dumps(document), encoding="utf-8")
    all_vars, custom_hosts = reconcile.load_topology(custom_topology)
    runner = FakeRunner(
        {
            int(host["vmid"]): exact_config_for(all_vars, host)
            for host in custom_hosts.values()
        }
    )

    assert invoke(
        tmp_path,
        runner,
        "plan",
        topology=custom_topology,
        mount_inspector=reconcile.inspect_mount_sources,
    ) == 0
    plan = json.loads(capsys.readouterr().out)
    mount_plans = {
        item["vmid"]: item["changes"]
        for item in plan["containers"]
        if item["changes"]
    }
    assert set(mount_plans) == set(expected_paths)
    assert all(
        changes == [
            {
                "after": {"group": group, "mode": "0700", "owner": owner},
                "before": None,
                "field": "mount_source:mp0",
                "operation": "reconcile",
                "risk": "safe",
            }
        ]
        for changes in mount_plans.values()
    )
    assert not any(path.exists() for path in expected_paths.values())

    assert invoke(
        tmp_path,
        runner,
        "apply",
        topology=custom_topology,
        mount_inspector=reconcile.inspect_mount_sources,
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["changed"] is True
    assert all(path.is_dir() for path in expected_paths.values())
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o777 == 0o700 for path in expected_paths.values())
    assert runner.calls == []


def test_mount_source_inspection_rejects_a_non_directory(tmp_path):
    source = tmp_path / "not-a-directory"
    source.write_text("data", encoding="utf-8")
    host = {
        "lxc_mounts": {
            "mp0": {
                "source": str(source),
                "source_owner": 0,
                "source_group": 0,
                "source_mode": "0700",
            }
        }
    }

    with pytest.raises(reconcile.ReconcileError, match="must be a regular directory"):
        reconcile.inspect_mount_sources(host)


def test_unmanaged_or_malformed_manual_confirmation_is_rejected(tmp_path, capsys):
    runner = FakeRunner()
    assert invoke(tmp_path, runner, "apply", "--allow-destructive", "999") == 2
    assert "unmanaged VMIDs: 999" in capsys.readouterr().err
    assert invoke(tmp_path, runner, "apply", "--allow-replacement", "all") == 2
    assert "exact numeric VMIDs" in capsys.readouterr().err
    assert runner.calls == []


def test_topology_validation_locks_three_units_and_unique_identities():
    all_vars, hosts = topology_data()
    assert {host["deployment_unit"] for host in hosts.values()} == {
        "tailnet",
        "apps-host",
        "openclaw-host",
    }

    duplicate = json.loads(json.dumps(hosts))
    duplicate["openclaw"]["vmid"] = duplicate["tailnet"]["vmid"]
    with pytest.raises(reconcile.ReconcileError, match="duplicate vmid"):
        reconcile.validate_topology(all_vars, duplicate)

    incomplete = dict(hosts)
    incomplete.pop("openclaw")
    with pytest.raises(reconcile.ReconcileError, match="exactly tailnet"):
        reconcile.validate_topology(all_vars, incomplete)

    malformed = json.loads(json.dumps(hosts))
    malformed["tailnet"].pop("description")
    with pytest.raises(reconcile.ReconcileError, match="tailnet.description"):
        reconcile.validate_topology(all_vars, malformed)


def test_malformed_topology_fails_closed_without_a_traceback(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"all": {}}', encoding="utf-8")

    result = reconcile.main(
        [
            "plan",
            "--topology",
            str(path),
            "--export-dir",
            str(tmp_path / "exports"),
        ],
        runner=FakeRunner(),
    )

    assert result == 2
    assert "topology lacks all.children.debian.hosts" in capsys.readouterr().err


def test_pve_plan_and_audit_gate_every_non_reconciler_mutation():
    plays = yaml.safe_load(
        (REPO_ROOT / "infra/ansible/playbooks/reconcile.yml").read_text(
            encoding="utf-8"
        )
    )
    tasks = plays[1]["tasks"]
    by_name = {task["name"]: task for task in tasks}
    apply_gate = "pve_lxc_reconcile_mode | default('apply') == 'apply'"
    for name in (
        "Reconcile PVE durable storage",
        "Reconcile LXC SSH and Python access through pct",
        "Read managed LXC SSH host keys through pct",
        "Create the controller SSH directory",
        "Reconcile managed LXC host keys on the controller",
        "Authenticate the configured deploy identity to every managed LXC",
    ):
        assert by_name[name]["when"] == ["homelab_unit == 'pve'", apply_gate]

    reconciler = by_name["Reconcile the three PVE LXC definitions"]
    assert reconciler["when"] == "homelab_unit == 'pve'"
    assert by_name["Preflight the complete PVE LXC apply before any unit mutation"][
        "vars"
    ] == {"pve_lxc_reconcile_preflight_only": True}
    assert reconciler["vars"] == {"pve_lxc_reconcile_preflight_only": False}
    names = [task["name"] for task in tasks]
    assert names.index(
        "Preflight the complete PVE LXC apply before any unit mutation"
    ) < names.index("Reconcile PVE durable storage") < names.index(
        "Reconcile the three PVE LXC definitions"
    )


def test_pve_storage_refuses_unknown_devices_and_wrong_mounts_before_data_ops():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra/ansible/roles/pve_homelab_storage/tasks/main.yml"
        ).read_text(encoding="utf-8")
    )
    assert len(tasks) == 1
    shell = tasks[0]["ansible.builtin.shell"]

    create = shell.index('if [ ! -e "${lv_path}" ]; then')
    format_new = shell.index('mkfs.ext4 -F "${lv_path}"')
    refuse_unknown = shell.index(
        "Refusing to format existing unrecognized device"
    )
    require_ext4 = shell.index('if [ "${filesystem_type}" != ext4 ]; then')
    verify_existing_mount = shell.index("Refusing data operations:")
    copy_data = shell.index('rsync -a "${mount_path}/" "${tmp_mount}/"')
    verify_final_mount = shell.index("Mounted source verification failed")
    manage_data = shell.index('managed_paths="')
    assert create < format_new < refuse_unknown < require_ext4
    assert verify_existing_mount < copy_data
    assert copy_data < verify_final_mount < manage_data

    canonical_fstab = 'expected_fstab="UUID=${uuid} ${mount_path} ext4 defaults,noatime 0 2"'
    assert canonical_fstab in shell
    assert "mktemp /etc/fstab.homelab.XXXXXX" in shell
    assert "NF < 2 || $2 != target" in shell
    assert shell.index('printf \'%s\\n\' "${expected_fstab}"') < shell.index(
        'mv -f -- "${fstab_tmp}" /etc/fstab'
    ) < copy_data
