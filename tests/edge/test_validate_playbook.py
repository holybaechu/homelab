from tests.helpers import REPO_ROOT


def test_validate_playbook_sources_caddy_environment():
    validate = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml"
    ).read_text(encoding="utf-8")

    assert ". /etc/conf.d/caddy" in validate
    assert "executable: /bin/sh" in validate


def test_validate_playbook_checks_adguard_webui_uses_caddy_tls_route():
    validate = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml"
    ).read_text(encoding="utf-8")

    assert '--resolve "adguard.home.hchu.me:443:{{ edge_ip }}"' in validate
    assert "https://adguard.home.hchu.me/login.html" in validate
    assert "Via: 1.1 Caddy" in validate


def test_validate_playbook_checks_private_home_dns_rewrite():
    validate = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml"
    ).read_text(encoding="utf-8")

    assert "drill @127.0.0.1 qbt.home.hchu.me A" in validate
    assert 'grep -F "{{ edge_ip }}"' in validate

