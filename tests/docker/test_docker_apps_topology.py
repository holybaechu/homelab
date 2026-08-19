import json
import yaml

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def topology():
    return json.loads(read("infra/ansible/inventory/prod/topology.json"))


def managed_lxcs():
    return topology()["all"]["children"]["debian"]["hosts"]


def test_tailnet_docker_apps_and_openclaw_are_the_managed_lxcs():
    hosts = managed_lxcs()

    assert set(hosts) == {"tailnet", "docker_apps", "openclaw"}
    assert (hosts["docker_apps"]["vmid"], hosts["docker_apps"]["ansible_host"]) == (
        110,
        "192.168.0.3",
    )
    assert (hosts["tailnet"]["vmid"], hosts["tailnet"]["ansible_host"]) == (
        111,
        "192.168.0.4",
    )
    expected_openclaw = {
        "vmid": 118,
        "hostname": "openclaw",
        "ansible_host": "192.168.0.5",
        "prefix_length": 24,
        "mac_address": "02:00:00:BA:EC:05",
        "startup_order": 3,
    }
    assert {
        field: hosts["openclaw"][field] for field in expected_openclaw
    } == expected_openclaw






def test_openclaw_lxc_has_local_docker_features_no_tun_and_only_ctf_scoped_mounts():
    options = managed_lxcs()["openclaw"]["lxc_root_options"]
    module = read("infra/opentofu/modules/pve-lxc/main.tf")
    rendered = json.dumps(options)

    assert "unprivileged  = true" in module
    assert "features {" not in module
    assert options["bind_mount_sources"] == ["{{ openclaw_ctf_shared_host_path }}"]
    assert "homelab_container_uid_offset" in options["bind_mount_source_owner"]
    assert "homelab_container_uid_offset" in options["bind_mount_source_group"]
    assert "service_uid" not in rendered
    assert "mount the dedicated CTF workspace once" in rendered
    assert "mount generated CTF sandbox skills once" not in rendered
    assert "enable nesting for the local OpenClaw CTF Docker Engine" in rendered
    assert "enable keyctl for the local OpenClaw CTF Docker Engine" in rendered
    assert "-features nesting=1,keyctl=1" in rendered
    assert "TUN device passthrough" in rendered
    assert "(path=)?/dev/net/tun" in rendered
    assert "obsolete or unexpected CTF bind mounts" in rendered
    assert "^mp[1-9][0-9]*:" in rendered
    assert sum(setting["delete_matching_keys"] for setting in options["absent_settings"]) == 2


def test_openclaw_is_in_debian_inventory_and_pve_bootstrap():
    inventory = topology()["all"]["children"]
    bootstrap = read("infra/ansible/roles/pve_lxc_access_bootstrap/tasks/main.yml")

    assert inventory["debian"]["hosts"]["openclaw"]["ansible_host"] == "192.168.0.5"
    assert set(inventory["svc_openclaw"]["hosts"]) == {"openclaw"}
    assert "loop: \"{{ groups['debian'] }}\"" in bootstrap
    assert 'vmid="{{ hostvars[item].vmid }}"' in bootstrap


def test_debian_bootstrap_materializes_proxmox_dns_before_apt():
    tasks = read("infra/ansible/roles/pve_lxc_access_bootstrap/tasks/main.yml")

    dns = tasks.index('nameservers="$(pct config')
    resolver = tasks.index("rm -f /etc/resolv.conf")
    apt = tasks.index("apt-get update", resolver)
    assert dns < resolver < apt
    assert "HOMELAB_SEARCH_DOMAIN" in tasks


def test_tracked_compose_manifests_follow_the_deployment_boundaries():
    root = REPO_ROOT / "apps" / "compose" / "homelab"
    assert (root / "compose.yml").exists()
    assert (root / ".env.example").exists()
    for retired in ("platform", "media", "code", "openclaw", "hermes", "game"):
        assert not (REPO_ROOT / "apps" / "compose" / retired).exists()

    variables = yaml.safe_load(
        read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")
    )
    assert not any(name.startswith("openclaw_") for name in variables)
    assert [project["name"] for project in variables["docker_compose_projects"]] == [
        "homelab"
    ]
    declared_roots = {
        project["src"].removeprefix("apps/compose/")
        for project in variables["docker_compose_projects"]
    }
    assert declared_roots == {"homelab"}
