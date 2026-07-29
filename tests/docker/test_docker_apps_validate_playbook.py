from tests.helpers import REPO_ROOT


def test_validation_checks_compose_dns_vpn_routes_and_hermes():
    validate = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(encoding="utf-8")

    assert "Validate Docker Compose application host" in validate
    assert "docker compose config --quiet" in validate
    assert "docker compose ps --services --status running" in validate
    assert "dig +short @127.0.0.1" in validate
    assert "qbt.home.hchu.me" in validate
    assert "copyparty.hchu.me" in validate
    assert "host_ip" in validate and "vpn_ip" in validate
    assert "hermes status" in validate


def test_validation_proves_vuetorrent_assets_config_and_route():
    validation = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(
        encoding="utf-8"
    )

    assert "test -f /vuetorrent/index.html" in validation
    assert "WebUI\\AlternativeUIEnabled=true" in validation
    assert "WebUI\\RootFolder=/vuetorrent" in validation
    assert "qbt.home.hchu.me" in validation
