from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ddns_script_exists_and_targets_cloudflare_api():
    script = REPO_ROOT / "apps" / "edge" / "ddns" / "update-cloudflare-ddns.sh"
    content = script.read_text(encoding="utf-8")

    assert "CLOUDFLARE_ZONE_ID" in content
    assert "CLOUDFLARE_DDNS_TOKEN" in content
    assert "DDNS_RECORD_NAMES" in content
    assert "api.cloudflare.com/client/v4/zones" in content
    assert "api.ipify.org" in content
