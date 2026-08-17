import yaml

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_tailnet_docker_apps_and_openclaw_are_the_managed_lxcs():
    topology = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    inventory = read("infra/ansible/inventory/prod/hosts.yml")

    assert topology.count("vmid             =") == 3
    assert "tailnet = {" in topology
    assert "docker_apps = {" in topology
    assert "openclaw = {" in topology
    assert "vmid             = 110" in topology
    assert 'ip_address       = "192.168.0.3/24"' in topology
    assert "vmid             = 111" in topology
    assert 'ip_address       = "192.168.0.4/24"' in topology
    assert "vmid             = 118" in topology
    assert 'hostname         = "openclaw"' in topology
    assert 'ip_address       = "192.168.0.5/24"' in topology
    assert 'mac_address      = "02:00:00:BA:EC:05"' in topology
    assert "startup_order    = 3" in topology






def test_openclaw_lxc_has_local_docker_features_no_tun_and_only_ctf_scoped_mounts():
    topology = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    module = read("infra/opentofu/modules/pve-lxc/main.tf")
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")
    openclaw = all_vars.split("  - vmid: 118", 1)[1].split("\npve_lxc_access_bootstrap:", 1)[0]

    assert "unprivileged  = true" in module
    assert "features {" not in module
    assert "device_passthrough" not in topology
    assert "mount_point" not in topology
    assert 'bind_mount_sources:' in openclaw
    assert "bind_mount_source_owner: \"{{ (homelab_container_uid_offset | int) + (openclaw_ctf_uid | int) }}\"" in openclaw
    assert "bind_mount_source_group: \"{{ (homelab_container_uid_offset | int) + (openclaw_ctf_gid | int) }}\"" in openclaw
    assert "service_uid" not in openclaw
    assert '"{{ openclaw_ctf_shared_host_path }}"' in openclaw
    assert "mount the dedicated CTF workspace once" in openclaw
    assert "-mp0 {{ openclaw_ctf_shared_host_path }},mp={{ openclaw_ctf_workspace_root }}" in openclaw
    assert "mount generated CTF sandbox skills once" not in openclaw
    assert "enable nesting for the local OpenClaw CTF Docker Engine" in openclaw
    assert "enable keyctl for the local OpenClaw CTF Docker Engine" in openclaw
    assert "-features nesting=1,keyctl=1" in openclaw
    assert "nesting or keyctl features" not in openclaw
    assert "TUN device passthrough" in openclaw
    assert "(path=)?/dev/net/tun" in openclaw
    assert "obsolete or unexpected CTF bind mounts" in openclaw
    assert "^mp[1-9][0-9]*:" in openclaw
    assert openclaw.count("delete_matching_keys: true") == 2


def test_openclaw_is_in_debian_inventory_and_pve_bootstrap():
    inventory = read("infra/ansible/inventory/prod/hosts.yml")
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")

    assert "        openclaw:\n          ansible_host: 192.168.0.5" in inventory
    assert "    svc_openclaw:\n      hosts:\n        openclaw:" in inventory
    assert "openclaw_ip: \"{{ hostvars['openclaw'].ansible_host }}\"" in all_vars
    bootstrap = all_vars.split("pve_lxc_access_bootstrap:", 1)[1]
    assert "  - vmid: 118\n    name: openclaw\n    os_family: debian" in bootstrap


def test_debian_bootstrap_materializes_proxmox_dns_before_apt():
    tasks = read("infra/ansible/roles/pve_lxc_access_bootstrap/tasks/main.yml")

    dns = tasks.index('nameservers="$(pct config')
    resolver = tasks.index("rm -f /etc/resolv.conf")
    apt = tasks.index("apt-get update", resolver)
    assert dns < resolver < apt
    assert "HOMELAB_SEARCH_DOMAIN" in tasks


def test_workloads_and_separate_arcane_control_plane_are_compose_projects():
    for project in ("platform", "media", "code", "openclaw"):
        root = REPO_ROOT / "apps" / "compose" / project
        assert (root / "compose.yml").exists()
        assert (root / ".env.example").exists()

    assert not (REPO_ROOT / "apps/compose/hermes").exists()

    arcane = REPO_ROOT / "apps" / "compose" / "arcane"
    assert (arcane / "compose.yml").exists()
    assert (arcane / ".env.example").exists()

    variables = read(
        "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    ).split("\ndocker_compose_projects:", 1)[1]
    assert "name: arcane" not in variables
    assert "name: hermes" not in variables
    assert "arcane_control_root:" in read(
        "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    )
    assert not (REPO_ROOT / "apps/compose/game").exists()
