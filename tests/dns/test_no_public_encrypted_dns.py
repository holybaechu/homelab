from tests.helpers import REPO_ROOT


def test_edge_caddyfile_does_not_expose_public_dns_hostname_or_doh_route():
    caddyfile = (REPO_ROOT / "apps" / "edge" / "Caddyfile").read_text(
        encoding="utf-8"
    )

    assert "dns.hchu.me {" not in caddyfile
    assert "handle /dns-query" not in caddyfile
    assert "tls_server_name dns.hchu.me" not in caddyfile


def test_dns_playbook_uses_private_adguard_certificate_name():
    site = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "site.yml").read_text(
        encoding="utf-8"
    )
    dns_vars = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "svc_dns.yml"
    ).read_text(encoding="utf-8")
    acme_script = (
        REPO_ROOT / "apps" / "dns" / "acme" / "renew-adguard-cert.sh"
    ).read_text(encoding="utf-8")

    assert "adguard_acme" in site
    assert "adguard_cert_domain: adguard.home.hchu.me" in dns_vars
    assert "adguard_cert_domain: dns.hchu.me" not in dns_vars
    assert "ADGUARD_CERT_DOMAIN:-adguard.home.hchu.me" in acme_script
    assert "ADGUARD_CERT_DOMAIN:-dns.hchu.me" not in acme_script
