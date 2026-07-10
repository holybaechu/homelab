from tests.helpers import REPO_ROOT


def test_validation_checks_compose_dns_vpn_routes_hermes_and_backup():
    validate = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(encoding="utf-8")

    assert "Validate Docker Compose application host" in validate
    assert "docker compose config --quiet" in validate
    assert "docker compose ps --services --status running" in validate
    assert "dig +short @127.0.0.1" in validate
    assert "qbt.home.hchu.me" in validate
    assert "copyparty.hchu.me" in validate
    assert "host_ip" in validate and "vpn_ip" in validate
    assert "hermes status" in validate
    assert "restic cat config" in validate
