from tests.helpers import REPO_ROOT


def test_ddns_updates_home_record_only():
    edge_vars = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "svc_edge.yml"
    ).read_text(encoding="utf-8")

    assert "ddns_record_names:\n  - home.hchu.me" in edge_vars
    assert "copyparty.hchu.me" not in edge_vars
    assert "dns.hchu.me" not in edge_vars

