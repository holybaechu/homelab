from tests.helpers import REPO_ROOT


def test_lxc_access_bootstrap_uses_inventory_hostvars_instead_of_a_second_host_list():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "pve_lxc_access_bootstrap"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "loop: \"{{ groups['debian'] }}\"" in tasks
    assert 'vmid="{{ hostvars[item].vmid }}"' in tasks
    assert "hostvars[item].os_type" in tasks
    assert "pve_lxc_access_bootstrap }}" not in tasks


def test_lxc_bootstrap_is_idempotent_and_does_not_always_restart_ssh():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "pve_lxc_access_bootstrap"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "changed=no" in tasks
    assert "changed=yes" in tasks
    assert 'changed_when: "\'changed=yes\'' in tasks
    assert "rc-service sshd restart" not in tasks


def test_bootstrap_collects_lxc_host_keys_from_proxmox_instead_of_keyscan():
    playbook = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "bootstrap.yml").read_text(encoding="utf-8")

    assert "ssh-keyscan" not in playbook
    assert "pct exec" in playbook
    assert "ssh_host_ed25519_key.pub" in playbook
    assert "ansible.builtin.known_hosts" in playbook


def test_bootstrap_waits_for_lxc_ssh_before_using_inventory_connections():
    playbook = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "bootstrap.yml").read_text(encoding="utf-8")

    assert "Wait for LXC SSH ports to accept connections" in playbook
    assert "ansible.builtin.wait_for" in playbook
    assert 'host: "{{ hostvars[item].ansible_host }}"' in playbook
    assert "port: 22" in playbook
    assert "timeout: 600" in playbook
    assert "delegate_to: localhost" in playbook


def test_base_reconciliation_runs_once_and_service_identity_is_opt_in():
    bootstrap = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "bootstrap.yml"
    ).read_text(encoding="utf-8")
    site = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "site.yml"
    ).read_text(encoding="utf-8")
    debian = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod"
        / "group_vars" / "debian.yml"
    ).read_text(encoding="utf-8")
    apps = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod"
        / "group_vars" / "svc_docker_apps.yml"
    ).read_text(encoding="utf-8")
    openclaw = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod"
        / "group_vars" / "svc_openclaw.yml"
    ).read_text(encoding="utf-8")

    assert "common_debian" not in bootstrap
    assert site.count("common_debian") == 1
    assert "common_debian_create_service_account: false" in debian
    assert "common_debian_create_service_account: true" in apps
    assert "common_debian_create_service_account: false" in openclaw


def test_openclaw_trust_uses_the_single_topology_without_a_shadow_target_list():
    playbook = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "trust-openclaw-lxc.yml"
    ).read_text(encoding="utf-8")

    assert "hostvars['openclaw'].vmid" in playbook
    assert "hostvars['openclaw'].ansible_host" in playbook
    assert "pve_lxc_access_bootstrap" not in playbook
    assert "pct" in playbook
    assert "ssh_host_ed25519_key.pub" in playbook
    assert "ansible.builtin.known_hosts" in playbook
    assert "role:" not in playbook


def test_debian_lxc_bootstrap_checks_each_required_package():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "pve_lxc_access_bootstrap"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "for pkg in openssh-server python3; do" in tasks
    assert "dpkg -s" in tasks
    assert "apt-get install -y openssh-server python3" in tasks
