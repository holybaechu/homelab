from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


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
    assert "Hash AdGuard admin password" in tasks
    assert "htpasswd" in tasks
    assert "adguard_admin_password_hash" in tasks


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
        / "dns.yml"
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
        / "dns.yml"
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

