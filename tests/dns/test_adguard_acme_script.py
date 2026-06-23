from tests.helpers import REPO_ROOT


def test_adguard_acme_script_is_noninteractive():
    script = (
        REPO_ROOT / "apps" / "dns" / "acme" / "renew-adguard-cert.sh"
    ).read_text(encoding="utf-8")

    assert "--accept-tos" in script
    assert "run || lego" not in script
    assert "ADGUARD_CERT_DOMAIN:-adguard.home.hchu.me" in script
    assert "ADGUARD_CERT_DOMAIN:-dns.hchu.me" not in script


def test_adguard_acme_restart_failure_is_not_hidden_when_service_exists():
    script = (
        REPO_ROOT / "apps" / "dns" / "acme" / "renew-adguard-cert.sh"
    ).read_text(encoding="utf-8")

    assert "rc-service adguardhome restart || true" not in script
    assert "if [ -x /etc/init.d/adguardhome ]; then" in script
    assert "rc-service adguardhome restart" in script
    assert "rc-service adguardhome status" in script


def test_adguard_acme_loop_survives_transient_renewal_failures():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard_acme"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "loop_command: /usr/local/bin/renew-adguard-cert" in tasks
    assert "AdGuard ACME renewal failed; retrying" in tasks
    loop_template = (
        REPO_ROOT / "infra" / "ansible" / "templates" / "openrc-loop.sh.j2"
    ).read_text(encoding="utf-8")
    assert 'loop_interval_seconds: "{{ adguard_acme_interval_seconds }}"' in tasks
    assert "if ! {{ loop_command }}; then" in loop_template
    assert "sleep {{ loop_interval_seconds }}" in loop_template
