import yaml
from jinja2 import Environment

from tests.helpers import REPO_ROOT


def render_maintenance_value(value, enabled):
    environment = Environment()
    environment.filters["bool"] = bool
    rendered = environment.from_string(str(value)).render(
        homelab_maintenance_upgrade=enabled
    )
    return yaml.safe_load(rendered)


def test_tailnet_lxc_disables_tailscale_dns_acceptance():
    inventory = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_tailnet.yml"
    ).read_text(encoding="utf-8")
    role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "tailscale_gateway"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "tailscale_accept_dns: false" in inventory
    assert "--accept-dns={{ tailscale_accept_dns | default(false) | lower }}" in role


def test_tailnet_enables_forwarding_while_public_ipv6_is_unroutable():
    role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "tailscale_gateway"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "net.ipv6.conf.all.disable_ipv6=1" in role
    assert "net.ipv6.conf.default.disable_ipv6=1" in role
    assert "sysctl -w" in role
    assert "net.ipv4.ip_forward=1" in role
    assert "net.ipv6.conf.all.forwarding=1" in role


def test_tailnet_join_passes_the_validated_auth_key_as_one_opaque_argv_value():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "roles"
            / "tailscale_gateway"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")
    )
    joins = [
        task
        for task in tasks
        if task.get("ansible.builtin.command", {}).get("argv", [])[:2]
        == ["tailscale", "up"]
    ]

    assert len(joins) == 1
    join = joins[0]
    command = join["ansible.builtin.command"]
    assert "cmd" not in command
    assert command["argv"].count("--auth-key={{ tailscale_auth_key }}") == 1
    assert join["no_log"] is True
    assert join["when"] == "tailscale_auth_key is defined"


def test_tailnet_manages_and_validates_persistent_udp_gro_forwarding():
    role_root = (
        REPO_ROOT / "infra" / "ansible" / "roles" / "tailscale_gateway"
    )
    tasks = yaml.safe_load(
        (role_root / "tasks" / "main.yml").read_text(encoding="utf-8")
    )
    by_name = {task["name"]: task for task in tasks}

    tooling = by_name["Install Tailscale gateway network tooling"][
        "ansible.builtin.apt"
    ]
    assert tooling["name"] == "ethtool"
    assert render_maintenance_value(tooling["state"], False) == "present"
    assert render_maintenance_value(tooling["state"], True) == "latest"

    script_task = by_name["Install the Tailscale UDP GRO configuration script"][
        "ansible.builtin.copy"
    ]
    assert script_task["dest"] == "/usr/local/sbin/configure-tailscale-udp-gro"
    assert script_task["mode"] == "0755"

    service_task = by_name["Install the persistent Tailscale UDP GRO service"][
        "ansible.builtin.copy"
    ]
    assert service_task["dest"] == "/etc/systemd/system/tailscale-udp-gro.service"
    assert service_task["mode"] == "0644"

    enabled = by_name["Enable persistent Tailscale UDP GRO forwarding"][
        "ansible.builtin.systemd_service"
    ]
    assert enabled == {
        "name": "tailscale-udp-gro.service",
        "enabled": True,
        "state": "started",
    }

    apply = by_name["Apply Tailscale UDP GRO forwarding on every deployment"]
    assert apply["ansible.builtin.command"]["argv"] == [
        "/usr/local/sbin/configure-tailscale-udp-gro"
    ]
    assert apply["changed_when"] is False

    script = (role_root / "files" / "configure-tailscale-udp-gro").read_text(
        encoding="utf-8"
    )
    assert "ip -o route get 8.8.8.8" in script
    assert 'rx-udp-gro-forwarding on rx-gro-list off' in script

    service = (role_root / "files" / "tailscale-udp-gro.service").read_text(
        encoding="utf-8"
    )
    assert "Before=tailscaled.service" in service
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/local/sbin/configure-tailscale-udp-gro" in service
    assert "RemainAfterExit=yes" in service
    assert "WantedBy=multi-user.target" in service

def test_tailscale_package_upgrades_only_during_explicit_maintenance():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "roles"
            / "tailscale_gateway"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")
    )
    package = next(
        task
        for task in tasks
        if task["name"] == "Install Tailscale without disrupting the active route"
    )["ansible.builtin.apt"]

    assert render_maintenance_value(package["state"], False) == "present"
    assert render_maintenance_value(package["update_cache"], False) is False
    assert render_maintenance_value(package["state"], True) == "latest"
    assert render_maintenance_value(package["update_cache"], True) is True
    assert render_maintenance_value(package["cache_valid_time"], False) is None
    assert render_maintenance_value(package["cache_valid_time"], True) == 3600


def test_tailscale_upgrade_defers_self_restart_and_recovers_stale_binary():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "roles"
            / "tailscale_gateway"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")
    )
    by_name = {task["name"]: task for task in tasks}

    package = by_name["Install Tailscale without disrupting the active route"]
    assert (
        render_maintenance_value(
            package["ansible.builtin.apt"]["state"], False
        )
        == "present"
    )
    assert (
        render_maintenance_value(
            package["ansible.builtin.apt"]["state"], True
        )
        == "latest"
    )
    assert package["ansible.builtin.apt"]["policy_rc_d"] == 101

    stale_check = by_name["Detect a tailscaled process using a replaced binary"]
    assert stale_check["failed_when"] is False
    assert "(deleted)" in stale_check["ansible.builtin.shell"]

    decision = by_name["Decide whether tailscaled must restart"]
    expression = decision["ansible.builtin.set_fact"]["tailscale_restart_required"]
    assert "tailscale_package.changed" in expression
    assert "tailscale_underlay.changed" in expression
    assert "tailscale_stale_binary.rc == 0" in expression
    assert "tailscale_previous_restart_guard.stat.exists" in expression

    restart_required = Environment().from_string(expression)
    cases = (
        (True, False, 1, False, True),
        (False, True, 1, False, True),
        (False, False, 0, False, True),
        (False, False, 1, True, True),
        (False, False, 1, False, False),
    )
    for package_changed, underlay_changed, stale_rc, previous_guard, expected in cases:
        rendered = restart_required.render(
            tailscale_package={"changed": package_changed},
            tailscale_underlay={"changed": underlay_changed},
            tailscale_stale_binary={"rc": stale_rc},
            tailscale_previous_restart_guard={"stat": {"exists": previous_guard}},
        )
        assert yaml.safe_load(rendered) is expected

    service_path = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "tailscale_gateway"
        / "files"
        / "tailscaled-ansible-restart.service"
    )
    timer_path = service_path.with_suffix(".timer")
    assert service_path.exists()
    assert timer_path.exists()

    service = service_path.read_text(encoding="utf-8")
    timer = timer_path.read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "RemainAfterExit=yes" in service
    assert "ExecStart=/usr/bin/systemctl restart tailscaled.service" in service
    assert "ExecStartPost=/bin/cp -- /run/homelab-tailscale-restart.request /run/homelab-tailscale-restart.completed" in service
    assert "ExecStartPost=/bin/rm -f -- /run/homelab-tailscale-restart.in-progress" in service
    assert "OnActiveSec=5s" in timer
    assert "AccuracySec=1us" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "RemainAfterElapse=no" in timer
    assert "Unit=tailscaled-ansible-restart.service" in timer

    task_names = [task["name"] for task in tasks]
    assert task_names.index("Cancel any pending tailscaled restart") < task_names.index(
        "Install Tailscale without disrupting the active route"
    )
    assert task_names.index("Mark this role run as interruption-sensitive") < task_names.index(
        "Install Tailscale without disrupting the active route"
    )
    assert task_names.index("Reset the deterministic tailscaled restart units") < task_names.index(
        "Schedule the deterministic tailscaled restart"
    )
    reset_failed = by_name["Reset the deterministic tailscaled restart units"]
    assert reset_failed["loop"] == [
        "tailscaled-ansible-restart.timer",
        "tailscaled-ansible-restart.service",
    ]
    assert "not loaded" in reset_failed["failed_when"]
    assert task_names.index("Remove the previous tailscaled restart completion proof") < task_names.index(
        "Write the requested tailscaled restart identifier"
    ) < task_names.index("Schedule the deterministic tailscaled restart")

    request = by_name["Write the requested tailscaled restart identifier"]
    assert request["ansible.builtin.copy"]["content"] == "{{ tailscale_restart_id }}\n"
    assert request["when"] == "tailscale_restart_required"

    clear_guard = by_name["Clear the interrupted-run guard when no restart is needed"]
    assert clear_guard["ansible.builtin.file"]["state"] == "absent"
    assert clear_guard["when"] == "not tailscale_restart_required"

    restart = by_name["Schedule the deterministic tailscaled restart"]
    systemd = restart["ansible.builtin.systemd_service"]
    assert systemd["name"] == "tailscaled-ansible-restart.timer"
    assert systemd["state"] == "started"
    assert restart["when"] == "tailscale_restart_required"
    assert "systemd-run" not in (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "tailscale_gateway"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")


def test_tailnet_restart_recovery_is_bounded_and_verifies_the_running_binary():
    plays = yaml.safe_load(
        (
            REPO_ROOT / "infra" / "ansible" / "playbooks" / "reconcile.yml"
        ).read_text(encoding="utf-8")
    )
    recovery = plays[1]["tasks"]
    by_name = {task["name"]: task for task in recovery}
    gate = [
        "homelab_unit == 'tailnet'",
        "tailscale_restart_scheduled | default(false)",
    ]

    wait = by_name["Wait for SSH after a scheduled tailscaled restart"]
    assert wait["when"] == gate
    assert wait["ansible.builtin.wait_for_connection"] == {
        "delay": 10,
        "connect_timeout": 5,
        "sleep": 5,
        "timeout": 180,
    }

    completion = by_name["Wait for the exact tailscaled restart proof"]
    assert completion["when"] == gate
    assert completion["changed_when"] is False
    assert completion["retries"] == 36
    assert completion["delay"] == 5
    assert completion["until"] == "tailscale_restart_completion.rc == 0"
    assert "/run/homelab-tailscale-restart.completed" in completion[
        "ansible.builtin.shell"
    ]
    assert "tailscale_restart_id" in completion["ansible.builtin.shell"]

    result = by_name["Verify the deterministic tailscaled restart succeeded"]
    assert result["changed_when"] is False
    assert result["failed_when"] == "tailscale_restart_result.stdout != 'success'"

    verify = by_name["Verify tailscaled runs the installed binary"]
    assert verify["when"] == gate
    assert verify["changed_when"] is False
    assert "/usr/sbin/tailscaled" in verify["ansible.builtin.shell"]

    task_names = [task["name"] for task in recovery]
    assert task_names.index("Wait for the exact tailscaled restart proof") < task_names.index(
        "Verify the deterministic tailscaled restart succeeded"
    ) < task_names.index("Verify tailscaled runs the installed binary")
