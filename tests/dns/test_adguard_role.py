from tests.helpers import REPO_ROOT


def test_adguard_role_installs_drill_for_dns_validation():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- drill" in tasks


def test_adguard_role_manages_nftables_firewall():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    handlers = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "handlers"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- nftables" in tasks
    assert "src: nftables.conf.j2" in tasks
    assert "dest: /etc/nftables.nft" in tasks
    assert "validate: nft -c -f %s" in tasks
    assert "name: nftables" in tasks
    assert "Restart adguard nftables" in handlers


def test_adguard_role_installs_htpasswd_for_password_hashing():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- apache2-utils" in tasks


def test_adguard_role_uses_versioned_download_and_extract_paths():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert 'dest: "/tmp/AdGuardHome_{{ adguard_version }}_{{ adguard_arch }}.tar.gz"' in tasks
    assert 'path: "/tmp/AdGuardHome-{{ adguard_version }}"' in tasks
    assert 'src: "/tmp/AdGuardHome_{{ adguard_version }}_{{ adguard_arch }}.tar.gz"' in tasks
    assert 'creates: "/tmp/AdGuardHome-{{ adguard_version }}/AdGuardHome/AdGuardHome"' in tasks
    assert 'src: "/tmp/AdGuardHome-{{ adguard_version }}/AdGuardHome/AdGuardHome"' in tasks
    assert "dest: /tmp/AdGuardHome.tar.gz" not in tasks
    assert "creates: /tmp/AdGuardHome/AdGuardHome" not in tasks


def test_adguard_role_hashes_plaintext_admin_password():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "adguard_admin_password is defined" in tasks
    assert "Read existing AdGuard admin password hash" in tasks
    assert "Verify existing AdGuard admin password hash" in tasks
    assert "Hash AdGuard admin password when rotation is required" in tasks
    assert "adguard_existing_admin_hash_verify.rc | default(1) != 0" in tasks
    assert "adguard_admin_password_hash" in tasks


def test_adguard_role_limits_plain_http_admin_ui_with_nftables():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    assert "- nftables" in tasks
    assert "Install AdGuard nftables config" in tasks
    assert "Enable AdGuard nftables" in tasks
    assert "ip saddr {{ edge_ip }} tcp dport {{ adguard_admin_port }} accept" in nftables
    assert "ip saddr {{ homelab_tailscale_cidr }} tcp dport {{ adguard_admin_port }} accept" in nftables
    assert "tcp dport {{ adguard_admin_port }} reject" in nftables


def test_adguard_role_uses_configured_admin_username():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    dns_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_dns.yml"
    ).read_text(encoding="utf-8")

    assert "adguard_admin_username: admin" in dns_vars
    assert "adguard_admin_username is defined" in tasks
    assert '"{{ adguard_admin_username }}"' in tasks
    assert "split(':', 1)[1]" in tasks
    assert "Update AdGuard admin user in existing config" in tasks
    assert "regex_replace('^admin:', '')" not in tasks


def test_adguard_role_updates_trusted_proxies_and_filters_in_existing_config():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    dns_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_dns.yml"
    ).read_text(encoding="utf-8")

    assert "adguard_trusted_proxies:" in dns_vars
    assert "  - 127.0.0.0/8" in dns_vars
    assert "  - ::1/128" in dns_vars
    assert '  - "{{ edge_ip }}/32"' in dns_vars
    assert "adguard_dns_filters:" in dns_vars
    assert "HaGeZi's Pro Blocklist" in dns_vars
    assert "filter_48.txt" in dns_vars
    assert "AdGuard DNS filter" not in dns_vars
    assert "Update AdGuard trusted proxies and DNS filters in existing config" in tasks
    assert "adguard_trusted_proxies | to_json" in tasks
    assert "adguard_dns_filters | to_json" in tasks

