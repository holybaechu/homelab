import json
import subprocess
import sys

import yaml

from tests.helpers import REPO_ROOT


def load_adguard_tasks():
    return yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "roles"
            / "adguard"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")
    )


def adguard_task(name: str):
    for task in load_adguard_tasks():
        if task["name"] == name:
            return task
    raise AssertionError(f"missing AdGuard task {name!r}")


def run_adguard_proxy_filter_updater(config_path):
    task = adguard_task(
        "Update AdGuard DNS, TLS, trusted proxies, and filters in existing config"
    )
    script = task["ansible.builtin.command"]["argv"][2]

    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config_path),
            json.dumps(["127.0.0.0/8", "::1/128", "192.168.0.4/32"]),
            json.dumps(
                [
                    {
                        "enabled": True,
                        "url": "https://adguardteam.github.io/HostlistsRegistry/assets/filter_48.txt",
                        "name": "HaGeZi's Pro Blocklist",
                        "id": 48,
                    }
                ]
            ),
            json.dumps(["tls://1.1.1.1", "tls://1.0.0.1"]),
            json.dumps(["1.1.1.1", "1.0.0.1"]),
            json.dumps(["1.1.1.1", "1.0.0.1"]),
            json.dumps(
                {
                    "enabled": True,
                    "server_name": "adguard.home.hchu.me",
                    "force_https": False,
                    "port_https": 443,
                    "port_dns_over_tls": 853,
                    "port_dns_over_quic": 853,
                    "certificate_path": "/opt/adguardhome/tls/fullchain.pem",
                    "private_key_path": "/opt/adguardhome/tls/privkey.pem",
                }
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


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
    assert 'checksum: "sha256:{{ adguard_sha256 }}"' in tasks
    assert 'path: "/tmp/AdGuardHome-{{ adguard_version }}"' in tasks
    assert 'src: "/tmp/AdGuardHome_{{ adguard_version }}_{{ adguard_arch }}.tar.gz"' in tasks
    assert 'creates: "/tmp/AdGuardHome-{{ adguard_version }}/AdGuardHome/AdGuardHome"' in tasks
    assert 'src: "/tmp/AdGuardHome-{{ adguard_version }}/AdGuardHome/AdGuardHome"' in tasks
    assert "dest: /tmp/AdGuardHome.tar.gz" not in tasks
    assert "creates: /tmp/AdGuardHome/AdGuardHome" not in tasks


def test_adguard_config_updater_replaces_inline_fallback_dns_without_duplicate(tmp_path):
    config = tmp_path / "AdGuardHome.yaml"
    config.write_text(
        "\n".join(
            [
                "dns:",
                "  upstream_dns:",
                "    - https://old.example/dns-query",
                "  bootstrap_dns:",
                "    - 9.9.9.9",
                "  fallback_dns: []",
                "  upstream_mode: load_balance",
                "tls:",
                "  enabled: false",
                "filters:",
                "  - enabled: false",
                "    url: https://old.example/filter.txt",
                '    name: "Old filter"',
                "    id: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_adguard_proxy_filter_updater(config)

    assert result.stdout.strip() == "changed"
    updated = config.read_text(encoding="utf-8")
    assert updated.count("  fallback_dns:") == 1
    assert "  fallback_dns: []" not in updated
    assert "  fallback_dns:\n    - 1.1.1.1\n    - 1.0.0.1" in updated
    assert "  upstream_mode: load_balance" in updated


def test_adguard_config_updater_collapses_existing_duplicate_fallback_dns(tmp_path):
    config = tmp_path / "AdGuardHome.yaml"
    config.write_text(
        "\n".join(
            [
                "dns:",
                "  upstream_dns:",
                "    - https://old.example/dns-query",
                "  bootstrap_dns:",
                "    - 9.9.9.9",
                "  fallback_dns: []",
                "  upstream_mode: load_balance",
                "  pending_requests:",
                "    enabled: true",
                "  fallback_dns:",
                "    - 1.1.1.1",
                "    - 1.0.0.1",
                "tls:",
                "  enabled: false",
                "filters:",
                "  - enabled: false",
                "    url: https://old.example/filter.txt",
                '    name: "Old filter"',
                "    id: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_adguard_proxy_filter_updater(config)

    assert result.stdout.strip() == "changed"
    updated = config.read_text(encoding="utf-8")
    assert updated.count("  fallback_dns:") == 1
    assert "  fallback_dns: []" not in updated
    assert "  pending_requests:\n    enabled: true" in updated


def test_adguard_release_checksum_is_pinned_in_inventory():
    dns_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_dns.yml"
    ).read_text(encoding="utf-8")

    assert "adguard_sha256: 9d77af61881fbcef04ba50d53fa80af16ce8dd9b02fdb4faea154b741d08c72b" in dns_vars



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
    assert "ip saddr {{ homelab_tailscale_cidr }} tcp dport {{ adguard_admin_port }} accept" not in nftables
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
    assert "Update AdGuard DNS, TLS, trusted proxies, and filters in existing config" in tasks
    assert "adguard_trusted_proxies | to_json" in tasks
    assert "adguard_dns_filters | to_json" in tasks


def test_adguard_role_updates_upstreams_fallbacks_and_tls_in_existing_config():
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

    assert "adguard_upstream_dns:" in dns_vars
    assert "  - tls://1.1.1.1" in dns_vars
    assert "  - tls://1.0.0.1" in dns_vars
    assert "adguard_fallback_dns:" in dns_vars
    assert "adguard_bootstrap_dns:" in dns_vars
    assert "adguard_upstream_dns | to_json" in tasks
    assert "adguard_bootstrap_dns | to_json" in tasks
    assert "adguard_fallback_dns | to_json" in tasks
    assert "adguard_tls_config | to_json" in tasks
    assert 'replace_nested_block(lines, "dns", "upstream_dns", upstream_dns_lines)' in tasks
    assert 'replace_nested_block(lines, "dns", "fallback_dns", fallback_dns_lines)' in tasks
    assert 'replace_top_level_block(lines, "tls", tls_lines)' in tasks

