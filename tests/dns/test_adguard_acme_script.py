from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_adguard_acme_script_is_noninteractive():
    script = (
        REPO_ROOT / "apps" / "dns" / "acme" / "renew-adguard-cert.sh"
    ).read_text(encoding="utf-8")

    assert "--accept-tos" in script
    assert "run || lego" not in script

