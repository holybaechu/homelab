from tests.helpers import REPO_ROOT


def test_alpine_lxc_bootstrap_opens_ssh_through_active_nftables():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "pve_lxc_access_bootstrap"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "Allow SSH through active Alpine nftables firewall" in tasks
    assert "rc-service nftables status" in tasks
    assert "nft list chain inet filter input" in tasks
    assert "ip saddr {{ homelab_lan_cidr }} tcp dport 22 accept" in tasks
    assert "ip saddr {{ homelab_tailscale_cidr }} tcp dport 22 accept" in tasks


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
