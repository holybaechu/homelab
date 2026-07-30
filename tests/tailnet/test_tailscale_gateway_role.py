import yaml
from jinja2 import Environment

from tests.helpers import REPO_ROOT


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


def test_tailnet_validation_checks_ip_forwarding():
    validation = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml"
    ).read_text(encoding="utf-8")

    assert "net.ipv4.ip_forward" in validation
    assert "net.ipv6.conf.all.forwarding" in validation
    assert 'tailnet_forwarding.stdout | trim != "1"' in validation


def test_tailscale_apt_package_upgrades_and_restarts_after_underlay_change():
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

    assert package["state"] == "latest"
    assert package["update_cache"] is True


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
    assert package["ansible.builtin.apt"]["state"] == "latest"
    assert package["ansible.builtin.apt"]["policy_rc_d"] == 101

    stale_check = by_name["Detect a tailscaled process using a replaced binary"]
    assert stale_check["failed_when"] is False
    assert "(deleted)" in stale_check["ansible.builtin.shell"]

    decision = by_name["Decide whether tailscaled must restart"]
    expression = decision["ansible.builtin.set_fact"]["tailscale_restart_required"]
    assert "tailscale_package.changed" in expression
    assert "tailscale_underlay.changed" in expression
    assert "tailscale_stale_binary.rc == 0" in expression

    restart_required = Environment().from_string(expression)
    cases = (
        (True, False, 1, True),
        (False, True, 1, True),
        (False, False, 0, True),
        (False, False, 1, False),
    )
    for package_changed, underlay_changed, stale_rc, expected in cases:
        rendered = restart_required.render(
            tailscale_package={"changed": package_changed},
            tailscale_underlay={"changed": underlay_changed},
            tailscale_stale_binary={"rc": stale_rc},
        )
        assert yaml.safe_load(rendered) is expected

    restart = by_name["Schedule a detached tailscaled restart"]
    argv = restart["ansible.builtin.command"]["argv"]
    assert argv[:3] == ["systemd-run", "--on-active=5s", "--collect"]
    assert argv[-3:] == ["/usr/bin/systemctl", "restart", "tailscaled"]
    assert restart["when"] == "tailscale_restart_required"


def test_tailnet_restart_recovery_is_bounded_and_verifies_the_running_binary():
    plays = yaml.safe_load(
        (
            REPO_ROOT / "infra" / "ansible" / "playbooks" / "site.yml"
        ).read_text(encoding="utf-8")
    )
    recovery = next(play for play in plays if play["name"] == "Wait for tailnet route recovery")
    by_name = {task["name"]: task for task in recovery["tasks"]}

    wait = by_name["Wait for SSH through the restarted subnet route"]
    assert wait["when"] == "tailscale_restart_scheduled | default(false)"
    assert wait["ansible.builtin.wait_for_connection"] == {
        "delay": 10,
        "connect_timeout": 5,
        "sleep": 5,
        "timeout": 180,
    }

    verify = by_name["Verify tailscaled is running the installed binary"]
    assert verify["when"] == "tailscale_restart_scheduled | default(false)"
    assert verify["changed_when"] is False
    assert 'tailscale_running_executable.stdout != "/usr/sbin/tailscaled"' in verify[
        "failed_when"
    ]
