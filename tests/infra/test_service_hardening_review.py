from tests.helpers import REPO_ROOT


def test_root_only_lxc_options_use_graceful_shutdown_before_stop():
    tasks = (REPO_ROOT / "infra/ansible/roles/pve_lxc_root_options/tasks/main.yml").read_text(encoding="utf-8")
    assert tasks.index("pct shutdown") < tasks.index("pct stop")


def test_storage_permissions_are_migrated_once_for_consolidated_uid_map():
    tasks = (REPO_ROOT / "infra/ansible/roles/pve_homelab_storage/tasks/main.yml").read_text(encoding="utf-8")
    assert ".homelab-two-lxc-permissions-v1" in tasks
    assert ".homelab-two-lxc-data-migrated-v1" in tasks
    assert "/var/lib/qbittorrent/.local/share/qBittorrent" in tasks
    assert "/var/lib/copyparty" in tasks
    assert "homelab_data_reconcile_permissions" in tasks
    assert "homelab_container_uid_offset + service_uid" in tasks


def test_retired_hermes_data_is_left_untouched():
    storage_tasks = (
        REPO_ROOT / "infra/ansible/roles/pve_homelab_storage/tasks/main.yml"
    ).read_text(encoding="utf-8")
    compose_tasks = (
        REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    ).read_text(encoding="utf-8")
    all_variables = (
        REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml"
    ).read_text(encoding="utf-8")
    app_variables = (
        REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    ).read_text(encoding="utf-8")

    for managed_text in (storage_tasks, compose_tasks, app_variables):
        assert "/srv/homelab/hermes" not in managed_text
    assert "hermes_service_uid" not in storage_tasks
    assert "hermes_service_uid" not in all_variables
    assert "hermes_service_uid" not in app_variables
    assert "retired_docker_data_paths" not in app_variables


def test_vpn_qbittorrent_storage_gets_mapped_ownership_after_v1_migration():
    tasks = (
        REPO_ROOT / "infra/ansible/roles/pve_homelab_storage/tasks/main.yml"
    ).read_text(encoding="utf-8")
    targeted_ownership = tasks.split('qbittorrent_vpn_path="', 1)[1].split(
        'mounted_vmid=""', 1
    )[0]

    assert "docker-apps/qbittorrent-vpn/qBittorrent" in tasks
    assert "stat -c '%u:%g'" in targeted_ownership
    assert 'chown -R "${app_uid}:${app_uid}" "${qbittorrent_vpn_path}"' in targeted_ownership
    assert tasks.index('qbittorrent_vpn_path="') < tasks.index(
        'if [ ! -e "${permissions_marker}" ]'
    )


def test_legacy_lxcs_stop_before_shared_storage_is_reowned():
    bootstrap = (REPO_ROOT / "infra/ansible/playbooks/bootstrap.yml").read_text(encoding="utf-8")
    retire = (REPO_ROOT / "infra/ansible/roles/pve_retire_legacy_lxcs/tasks/main.yml").read_text(encoding="utf-8")
    assert bootstrap.index("pve_retire_legacy_lxcs") < bootstrap.index("pve_homelab_storage")
    assert "pct shutdown" in retire
    assert retire.index("pct shutdown") < retire.index("pct stop")


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


def test_apt_owned_runtime_dependencies_upgrade_on_deploy():
    common = (REPO_ROOT / "infra/ansible/roles/common_debian/tasks/main.yml").read_text(encoding="utf-8")
    docker = (REPO_ROOT / "infra/ansible/roles/docker_engine/tasks/main.yml").read_text(encoding="utf-8")
    tailscale = (REPO_ROOT / "infra/ansible/roles/tailscale_gateway/tasks/main.yml").read_text(encoding="utf-8")

    assert "Install Debian base packages" in common
    assert "state: latest" in common.split("- name: Set timezone", 1)[0]
    assert "state: latest" in docker.split("- name: Configure Docker daemon defaults", 1)[0]
    assert "state: latest" in tailscale.split("- name: Disable unusable public IPv6", 1)[0]
    assert "update_cache: true" in common
    assert "update_cache: true" in docker
    assert "update_cache: true" in tailscale
