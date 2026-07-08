from tests.helpers import REPO_ROOT


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_compose_projects_are_tracked_under_apps_compose():
    for project in ("traefik", "media", "game"):
        assert (REPO_ROOT / "apps" / "compose" / project / "compose.yml").exists()
        assert (REPO_ROOT / "apps" / "compose" / project / ".env.example").exists()


def test_docker_apps_iac_topology_exists_and_keeps_recovery_planes_separate():
    topology = _read("infra/opentofu/envs/prod/containers.auto.tfvars")
    inventory = _read("infra/ansible/inventory/prod/hosts.yml")
    all_vars = _read("infra/ansible/inventory/prod/group_vars/all.yml")
    site = _read("infra/ansible/playbooks/site.yml")

    assert "docker_apps = {" in topology
    assert 'hostname         = "docker-apps"' in topology
    assert "docker_apps:" in inventory
    assert "svc_docker_apps:" in inventory
    assert "docker_apps_ip: \"{{ hostvars['docker_apps'].ansible_host }}\"" in all_vars
    assert "hosts: svc_docker_apps" in site

    # Network/control-plane services stay independently managed as LXCs.
    assert "tailnet = {" in topology
    assert "hermes = {" in topology
    assert "hosts: svc_tailnet" in site
    assert "hosts: svc_hermes" in site


def test_docker_roles_are_present():
    assert (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_engine" / "tasks" / "main.yml").exists()
    assert (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_compose_project" / "tasks" / "main.yml").exists()


def test_docker_apps_root_options_mount_existing_shared_storage_once():
    all_vars = _read("infra/ansible/inventory/prod/group_vars/all.yml")
    docker_apps = all_vars.split("  - vmid: 117", maxsplit=1)[1].split("pve_lxc_access_bootstrap:", maxsplit=1)[0]

    assert "name: docker_apps" in docker_apps
    assert "pass through tun device for gluetun" in docker_apps
    assert "enable nesting for Docker Engine in LXC" in docker_apps
    assert "mp=/downloads" in docker_apps
    assert "mp=/public" in docker_apps
    assert "mp=/shared-readonly,ro=1" in docker_apps
    assert "mp=/minecraft" in docker_apps
