from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_adguard_acme_script_is_noninteractive():
    script = (
        REPO_ROOT / "apps" / "dns" / "acme" / "renew-adguard-cert.sh"
    ).read_text(encoding="utf-8")

    assert "--accept-tos" in script
    assert "run || lego" not in script


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

    assert "if ! /usr/local/bin/renew-adguard-cert; then" in tasks
    assert "AdGuard ACME renewal failed; retrying" in tasks
    assert "sleep {{ adguard_acme_interval_seconds }}" in tasks
