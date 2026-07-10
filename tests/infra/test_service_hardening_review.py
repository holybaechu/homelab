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
    assert "homelab_container_uid_offset + hermes_service_uid" in tasks


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
