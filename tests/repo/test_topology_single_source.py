from tests.helpers import REPO_ROOT


def test_ansible_ip_variables_cover_only_managed_lxcs():
    text = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml").read_text(encoding="utf-8")
    assert "tailnet_ip: \"{{ hostvars['tailnet'].ansible_host }}\"" in text
    assert "docker_apps_ip: \"{{ hostvars['docker_apps'].ansible_host }}\"" in text
    for retired in ("edge", "dns", "downloads", "files", "minecraft", "hermes"):
        assert f"{retired}_ip:" not in text


def test_topology_helper_is_shared_by_inventory_runner_and_plan_guard():
    for path in (
        "scripts/ci/render_ansible_inventory.py",
        "scripts/ci/render_ansible_targets.py",
        "scripts/ci/check_tofu_plan_safe.py",
    ):
        assert "homelab_topology import" in (REPO_ROOT / path).read_text(encoding="utf-8")
