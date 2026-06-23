from tests.helpers import REPO_ROOT


def test_ansible_ip_variables_are_derived_from_inventory_hostvars():
    all_vars = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml").read_text(encoding="utf-8")

    for service in ("edge", "dns", "tailnet", "downloads", "files", "minecraft", "hermes"):
        assert f"{service}_ip: \"{{{{ hostvars['{service}'].ansible_host }}}}\"" in all_vars

    assert "edge_ip: 192.168.0.4" not in all_vars
    assert "dns_ip: 192.168.0.3" not in all_vars


def test_topology_helper_is_shared_by_inventory_runner_and_plan_guard():
    inventory = (REPO_ROOT / "scripts" / "ci" / "render_ansible_inventory.py").read_text(encoding="utf-8")
    targets = (REPO_ROOT / "scripts" / "ci" / "render_ansible_targets.py").read_text(encoding="utf-8")
    guard = (REPO_ROOT / "scripts" / "ci" / "check_tofu_plan_safe.py").read_text(encoding="utf-8")

    assert "from homelab_topology import" in inventory
    assert "from homelab_topology import" in targets
    assert "from homelab_topology import expected_lxc_count" in guard
    assert 'os.environ.get("TOFU_EXPECTED_LXC_COUNT", "7")' not in guard
