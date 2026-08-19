from jinja2 import Environment
import yaml

from tests.helpers import REPO_ROOT


def test_root_only_lxc_options_use_graceful_shutdown_before_stop():
    tasks = (REPO_ROOT / "infra/ansible/roles/pve_lxc_root_options/tasks/main.yml").read_text(encoding="utf-8")
    assert tasks.index("pct shutdown") < tasks.index("pct stop")
    assert "hostvars[item].lxc_root_options.absent_settings | default([])" in tasks
    assert "loop: \"{{ groups['debian'] }}\"" in tasks
    assert "pct set \"${vmid}\" {{ setting.pct_args }}" in tasks
    assert tasks.index("trap restart_if_needed EXIT") < tasks.index("pct shutdown")
    assert 'restart=0\n    fi\n    trap - EXIT' in tasks


def test_storage_permissions_are_migrated_once_for_consolidated_uid_map():
    tasks = (REPO_ROOT / "infra/ansible/roles/pve_homelab_storage/tasks/main.yml").read_text(encoding="utf-8")
    assert ".homelab-two-lxc-permissions-v1" in tasks
    assert ".homelab-two-lxc-data-migrated-v1" in tasks
    assert "/var/lib/qbittorrent/.local/share/qBittorrent" in tasks
    assert "/var/lib/copyparty" in tasks
    assert "homelab_data_reconcile_permissions" in tasks
    assert "homelab_container_uid_offset + service_uid" in tasks






def test_docker_host_releases_port_53_for_adguard():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_engine/tasks/main.yml").read_text(encoding="utf-8")
    assert "DNSStubListener=no" in tasks
    assert "/run/systemd/resolve/resolv.conf" in tasks


def test_compose_role_refuses_to_write_without_shared_mount():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    assert "mountpoint -q /srv/homelab" in tasks


def test_tailscale_join_is_idempotent():
    tasks = (REPO_ROOT / "infra/ansible/roles/tailscale_gateway/tasks/main.yml").read_text(encoding="utf-8")
    join = tasks.split("- name: Join Tailscale when auth key is supplied", 1)[1]
    assert "changed_when: false" in join


def test_apt_owned_runtime_dependencies_upgrade_only_in_maintenance():
    environment = Environment()
    environment.filters["bool"] = bool

    for role in ("common_debian", "docker_engine", "tailscale_gateway"):
        tasks = yaml.safe_load(
            (
                REPO_ROOT
                / "infra"
                / "ansible"
                / "roles"
                / role
                / "tasks"
                / "main.yml"
            ).read_text(encoding="utf-8")
        )
        package_modules = [
            task["ansible.builtin.apt"]
            for task in tasks
            if "ansible.builtin.apt" in task
            and "name" in task["ansible.builtin.apt"]
        ]
        assert package_modules
        for package in package_modules:
            routine_state = environment.from_string(
                package["state"]
            ).render(homelab_maintenance_upgrade=False)
            maintenance_state = environment.from_string(
                package["state"]
            ).render(homelab_maintenance_upgrade=True)
            assert routine_state.strip() == "present"
            assert maintenance_state.strip() == "latest"
