from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_only_tailnet_and_docker_apps_are_managed_lxcs():
    topology = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    inventory = read("infra/ansible/inventory/prod/hosts.yml")

    assert topology.count("vmid             =") == 2
    assert "tailnet = {" in topology
    assert "docker_apps = {" in topology
    for retired in ("dns", "edge", "downloads", "files", "minecraft", "hermes"):
        assert f"  {retired} = {{" not in topology
        assert f"svc_{retired}:" not in inventory

    assert "vmid             = 110" in topology
    assert 'ip_address       = "192.168.0.3/24"' in topology
    assert "vmid             = 111" in topology
    assert 'ip_address       = "192.168.0.4/24"' in topology


def test_retired_lxcs_are_forgotten_without_destruction():
    main = read("infra/opentofu/envs/prod/main.tf")
    assert 'module "target_lxc"' in main
    assert "from = module.active_lxc\n" in main
    assert "from = module.lxc\n" in main
    assert main.count("destroy = false") == 2


def test_only_tailnet_keeps_tun_and_docker_lxc_removes_its_retired_device():
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")
    tailnet = all_vars.split("  - vmid: 111", 1)[1].split("  - vmid: 110", 1)[0]
    docker = all_vars.split("  - vmid: 110", 1)[1].split("pve_lxc_access_bootstrap:", 1)[0]

    assert "pass through tun device for Tailscale" in tailnet
    assert "retired Gluetun TUN device" in docker
    assert "absent_settings:" in docker
    assert "pct_args: '-delete dev0'" in docker
    assert "pass through tun device for Gluetun" not in docker
    assert "enable nesting for Docker Engine in LXC" in docker
    assert "-mp0 /var/lib/homelab,mp=/srv/homelab" in docker
    assert "-mp1" not in docker


def test_debian_bootstrap_materializes_proxmox_dns_before_apt():
    tasks = read("infra/ansible/roles/pve_lxc_access_bootstrap/tasks/main.yml")

    dns = tasks.index('nameservers="$(pct config')
    resolver = tasks.index("rm -f /etc/resolv.conf")
    apt = tasks.index("apt-get update", resolver)
    assert dns < resolver < apt
    assert "HOMELAB_SEARCH_DOMAIN" in tasks


def test_low_id_cutover_is_hostname_guarded_and_locally_archived():
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")
    tasks = read("infra/ansible/roles/pve_prepare_low_id_cutover/tasks/main.yml")
    playbook = read("infra/ansible/playbooks/prepare-low-id-cutover.yml")

    assert "target_vmid: 110" in all_vars
    assert "legacy_name: edge" in all_vars
    assert "target_vmid: 111" in all_vars
    assert "legacy_name: dns" in all_vars
    assert "desired_name: docker-apps, source_vmid: 117, backup_mode: stop" in all_vars
    assert "desired_name: tailnet, source_vmid: 112, backup_mode: snapshot" in all_vars
    assert "vzdump" in tasks
    assert "pct shutdown" in tasks
    assert "status: stopped" in tasks
    assert 'pct destroy "$vmid"' in tasks
    assert "low_id_cutover_confirmed" in tasks
    assert playbook.index("pve_retire_legacy_lxcs") < playbook.index("pve_homelab_storage")
    assert playbook.index("pve_homelab_storage") < playbook.index("pve_prepare_low_id_cutover")
    assert "name: restic" in playbook
    assert "state: absent" in playbook


def test_source_pair_is_retired_only_after_a_failback_guarded_route_handoff():
    workflow = read(".github/workflows/cd.yml")
    tasks = read("infra/ansible/roles/pve_finalize_low_id_cutover/tasks/main.yml")

    assert workflow.index("Validate services") < workflow.index("Arm failback")
    assert workflow.index("Arm failback") < workflow.index("Prove Proxmox remains reachable")
    assert workflow.index("Prove Proxmox remains reachable") < workflow.index("Retire the archived source pair")
    assert "homelab-tailnet-source-failback" in tasks
    assert "--on-active=5m" in tasks
    assert "pct status 112 | grep -q 'status: stopped'" in tasks
    assert "without a local vzdump archive" in tasks
    assert 'pct destroy "$vmid"' in tasks


def test_legacy_application_lxcs_are_archived_before_final_retirement():
    tasks = read("infra/ansible/roles/pve_finalize_low_id_cutover/tasks/main.yml")
    variables = read("infra/ansible/inventory/prod/group_vars/all.yml")

    archive = tasks.index("Archive stopped legacy application LXCs")
    retire = tasks.index("Destroy only archive-verified legacy application LXCs")
    assert archive < retire
    legacy_tasks = tasks[archive:]
    assert 'loop: "{{ legacy_lxcs }}"' in legacy_tasks
    assert "pre-two-lxc-retirement-{{ item.name }}" in legacy_tasks
    assert legacy_tasks.count('status: stopped') >= 2
    assert 'vzdump "$vmid"' in legacy_tasks
    assert 'name "vzdump-lxc-${vmid}-*.tar.zst"' in legacy_tasks
    assert 'pct destroy "$vmid" --purge 1 --destroy-unreferenced-disks 1' in legacy_tasks
    assert "{name: hermes, vmid: 116}" in variables


def test_workloads_and_separate_arcane_control_plane_are_compose_projects():
    for project in ("platform", "media"):
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
