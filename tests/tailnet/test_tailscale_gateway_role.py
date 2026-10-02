from configparser import ConfigParser
import os
from pathlib import PurePosixPath
import re
import shlex
import subprocess

from tests.helpers import (
    REPO_ROOT, load_yaml, posix_shell, render_ansible, task_enabled, task_with_module,
    write_tool,
)


ROLE = REPO_ROOT / "infra/ansible/roles/tailscale_gateway"
RESTART = "tailscaled-ansible-restart"
GUARD = "/run/homelab-tailscale-restart.in-progress"
PROOF = "/run/homelab-tailscale-restart.completed"
REQUEST = "/run/homelab-tailscale-restart.request"


def role_tasks():
    return load_yaml(ROLE / "tasks/main.yml")


def command_task(tasks, prefix):
    return task_with_module([
        task for task in tasks
        if task.get("ansible.builtin.command", {}).get("argv", [])[:len(prefix)] == prefix
    ], "ansible.builtin.command")


def unit_section(filename, section):
    unit = ConfigParser(strict=False, interpolation=None)
    unit.read(ROLE / "files" / filename, encoding="utf-8")
    return unit[section]


def duration_seconds(value):
    number, unit = re.fullmatch(r"(\d+(?:\.\d+)?)(us|ms|s)?", value).groups()
    return float(number) * {"us": 0.000001, "ms": 0.001, "s": 1, None: 1}[unit]


def test_tailnet_persists_and_applies_routing_policy():
    tasks = role_tasks()
    expected = {
        "net.ipv6.conf.all.disable_ipv6=1",
        "net.ipv6.conf.default.disable_ipv6=1",
        "net.ipv4.ip_forward=1",
        "net.ipv6.conf.all.forwarding=1",
    }
    persisted = {
        line.strip()
        for task in tasks
        if task.get("ansible.builtin.copy", {}).get("dest", "").startswith("/etc/sysctl.d/")
        for line in task["ansible.builtin.copy"]["content"].splitlines()
    }
    apply = task_with_module([
        task for task in tasks
        if task.get("ansible.builtin.command", {}).get("cmd", "").startswith("sysctl ")
    ], "ansible.builtin.command")
    assert expected <= persisted
    assert expected <= set(shlex.split(apply["ansible.builtin.command"]["cmd"]))


def test_tailnet_join_keeps_dns_local_and_auth_key_opaque_and_private():
    join = command_task(role_tasks(), ["tailscale", "up"])
    inventory = load_yaml(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_tailnet.yml")
    key = "tskey-auth-fixture-123"
    argv = [render_ansible(value, **inventory, tailscale_auth_key=key)
            for value in join["ansible.builtin.command"]["argv"]]

    assert "--accept-dns=false" in argv
    assert argv.count("--auth-key=" + key) == 1
    assert "cmd" not in join["ansible.builtin.command"]
    assert join["no_log"] is True
    assert task_enabled(join, tailscale_auth_key=key)
    assert not task_enabled(join)


def test_tailnet_keeps_udp_gro_forwarding_persistent():
    tasks = role_tasks()
    script = task_with_module(tasks, "ansible.builtin.copy",
                              dest="/usr/local/sbin/configure-tailscale-udp-gro")
    assert script["ansible.builtin.copy"]["mode"] == "0755"
    assert (ROLE / "files" / script["ansible.builtin.copy"]["src"]).is_file()
    installed = task_with_module(tasks, "ansible.builtin.copy",
                                 dest="/etc/systemd/system/tailscale-udp-gro.service")
    service = unit_section(installed["ansible.builtin.copy"]["src"], "Service")
    assert service["ExecStart"] == "/usr/local/sbin/configure-tailscale-udp-gro"
    assert service["Type"] == "oneshot"
    unit_file = installed["ansible.builtin.copy"]["src"]
    assert "tailscaled.service" in unit_section(unit_file, "Unit")["Before"].split()
    assert "multi-user.target" in unit_section(unit_file, "Install")["WantedBy"].split()
    enabled = task_with_module(tasks, "ansible.builtin.systemd_service",
                               name="tailscale-udp-gro.service")
    assert enabled["ansible.builtin.systemd_service"]["enabled"] is True
    assert enabled["ansible.builtin.systemd_service"]["state"] == "started"
    command_task(tasks, [service["ExecStart"]])
    script_source = (ROLE / "files" / script["ansible.builtin.copy"]["src"]).read_text(encoding="utf-8")
    assert "rx-udp-gro-forwarding on rx-gro-list off" in script_source


def test_tailscale_upgrade_preserves_the_route_and_recovers_interrupted_runs():
    tasks = role_tasks()
    package = task_with_module(tasks, "ansible.builtin.apt", name="tailscale")
    assert package["ansible.builtin.apt"]["policy_rc_d"] == 101
    cancel = command_task(tasks, ["systemctl", "stop"])
    guard = task_with_module(tasks, "ansible.builtin.copy", dest=GUARD)
    assert {RESTART + ".timer", RESTART + ".service"} <= set(cancel["ansible.builtin.command"]["argv"])
    assert tasks.index(cancel) < tasks.index(guard) < tasks.index(package)

    decision = next(task["ansible.builtin.set_fact"]["tailscale_restart_required"]
                    for task in tasks
                    if "tailscale_restart_required" in task.get("ansible.builtin.set_fact", {}))
    for package_changed, underlay_changed, stale_rc, previous_guard, expected in (
        (True, False, 1, False, True),
        (False, True, 1, False, True),
        (False, False, 0, False, True),
        (False, False, 1, True, True),
        (False, False, 1, False, False),
    ):
        assert render_ansible(
            decision, tailscale_package={"changed": package_changed},
            tailscale_underlay={"changed": underlay_changed},
            tailscale_stale_binary={"rc": stale_rc},
            tailscale_previous_restart_guard={"stat": {"exists": previous_guard}},
        ) is expected

    reset = command_task(tasks, ["systemctl", "reset-failed"])
    for rc, stderr, failed in ((0, "", False), (1, "unit not loaded", False), (1, "denied", True)):
        assert render_ansible("{{ " + reset["failed_when"] + " }}",
                              tailscale_reset_failed={"rc": rc, "stderr": stderr}) is failed
    clear_proof = task_with_module(tasks, "ansible.builtin.file", path=PROOF)
    request = task_with_module(tasks, "ansible.builtin.copy", dest=REQUEST)
    restart = task_with_module(tasks, "ansible.builtin.systemd_service", name=RESTART + ".timer")
    assert set(reset["loop"]) == {RESTART + ".timer", RESTART + ".service"}
    assert clear_proof["ansible.builtin.file"]["state"] == "absent"
    assert render_ansible(request["ansible.builtin.copy"]["content"], tailscale_restart_id="this-run").strip() == "this-run"
    assert restart["ansible.builtin.systemd_service"]["state"] == "started"
    assert tasks.index(reset) < tasks.index(restart)
    assert tasks.index(clear_proof) < tasks.index(request) < tasks.index(restart)
    for task in (reset, clear_proof, request, restart):
        assert task_enabled(task, tailscale_restart_required=True)
        assert not task_enabled(task, tailscale_restart_required=False)
    clear_guard = task_with_module(tasks, "ansible.builtin.file", path=GUARD)
    assert clear_guard["ansible.builtin.file"]["state"] == "absent"
    assert task_enabled(clear_guard, tailscale_restart_required=False)
    assert not task_enabled(clear_guard, tailscale_restart_required=True)

    service = unit_section(RESTART + ".service", "Service")
    timer = unit_section(RESTART + ".timer", "Timer")
    assert service["Type"] == "oneshot"
    assert service.getboolean("RemainAfterExit") is True
    command = shlex.split(service["ExecStart"])
    assert PurePosixPath(command[0]).name == "systemctl"
    assert command[1:] == ["restart", "tailscaled.service"]
    assert 0 < duration_seconds(service["TimeoutStartSec"]) <= 180
    assert timer["Unit"] == RESTART + ".service"
    assert duration_seconds(timer["RandomizedDelaySec"]) == 0
    assert 0 < duration_seconds(timer["OnActiveSec"]) + duration_seconds(timer["AccuracySec"]) <= 10
    assert timer.getboolean("RemainAfterElapse") is False
    source = (ROLE / "files" / (RESTART + ".service")).read_text(encoding="utf-8")
    assert f"/bin/cp -- {REQUEST} {PROOF}" in source
    assert f"/bin/rm -f -- {GUARD}" in source


def test_tailnet_restart_recovery_is_bounded_and_checks_this_run(tmp_path):
    tasks = role_tasks()
    restart = task_with_module(tasks, "ansible.builtin.systemd_service", name=RESTART + ".timer")
    wait = task_with_module(tasks, "ansible.builtin.wait_for_connection")
    completion = next(task for task in tasks if task.get("register") == "tailscale_restart_completion")
    result = next(task for task in tasks if task.get("register") == "tailscale_restart_result")
    verify = next(task for task in tasks
                  if "/usr/sbin/tailscaled" in task.get("ansible.builtin.shell", "")
                  and task.get("failed_when") is not False)
    assert 0 < wait["ansible.builtin.wait_for_connection"]["timeout"] <= 180
    assert 0 < completion["retries"] * completion["delay"] <= 180
    assert PROOF in completion["ansible.builtin.shell"]
    assert "tailscale_restart_id" in completion["ansible.builtin.shell"]
    assert render_ansible("{{ " + completion["until"] + " }}", tailscale_restart_completion={"rc": 0}) is True
    assert render_ansible("{{ " + completion["until"] + " }}", tailscale_restart_completion={"rc": 1}) is False
    for status, failed in (("success", False), ("failed", True)):
        assert render_ansible("{{ " + result["failed_when"] + " }}",
                              tailscale_restart_result={"stdout": status}) is failed
    stale = next(task for task in tasks if task.get("register") == "tailscale_stale_binary")
    assert stale["failed_when"] is False
    tools = tmp_path / "bin"
    tools.mkdir()
    write_tool(tools / "systemctl", "printf '%s\\n' 123\n")
    write_tool(tools / "readlink",
               'test "$1" = /proc/123/exe\nprintf "%s\\n" "$TEST_RUNNING_BINARY"\n')
    environment = os.environ.copy()
    environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
    for binary, stale_rc, verify_rc in (
        ("/usr/sbin/tailscaled", 1, 0),
        ("/usr/sbin/tailscaled (deleted)", 0, 1),
        ("/usr/bin/unexpected", 0, 1),
    ):
        environment["TEST_RUNNING_BINARY"] = binary
        for task, expected in ((stale, stale_rc), (verify, verify_rc)):
            probe = subprocess.run(
                [posix_shell(), "-c", task["ansible.builtin.shell"]],
                env=environment, text=True, capture_output=True, check=False,
            )
            assert probe.returncode == expected, probe.stdout + probe.stderr
    for task in (wait, completion, result, verify):
        assert task_enabled(task, homelab_unit="tailnet", tailscale_restart_scheduled=True)
        assert not task_enabled(task, homelab_unit="tailnet", tailscale_restart_scheduled=False)
        assert not task_enabled(task, homelab_unit="apps-host", tailscale_restart_scheduled=True)
    assert tasks.index(restart) < tasks.index(wait) < tasks.index(completion) < tasks.index(result) < tasks.index(verify)
