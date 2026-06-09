from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_caddyfile_is_tracked_and_uses_current_lxc_ips():
    caddyfile = REPO_ROOT / "apps" / "edge" / "Caddyfile"
    content = caddyfile.read_text(encoding="utf-8")

    assert "reverse_proxy 192.168.0.7:3923" in content
    assert "reverse_proxy 192.168.0.3:80" in content
    assert "reverse_proxy 192.168.0.6:8080" in content
    assert "reverse_proxy https://192.168.0.2:8006" in content
    assert "192.168.0.14" not in content
