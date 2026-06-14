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


def test_proxmox_route_allows_same_origin_frames_for_web_shell():
    caddyfile = REPO_ROOT / "apps" / "edge" / "Caddyfile"
    content = caddyfile.read_text(encoding="utf-8")
    pve_block = content.split("pve.home.hchu.me {", maxsplit=1)[1].split(
        "router.home.hchu.me {", maxsplit=1
    )[0]

    assert '(pve_headers)' in content
    assert 'X-Frame-Options "SAMEORIGIN"' in content
    assert "import pve_headers" in pve_block
    assert "import secure_headers" not in pve_block


def test_router_route_omits_nosniff_for_mislabelled_flutter_assets():
    caddyfile = REPO_ROOT / "apps" / "edge" / "Caddyfile"
    content = caddyfile.read_text(encoding="utf-8")
    router_block = content.split("router.home.hchu.me {", maxsplit=1)[1].split(
        "dns.hchu.me {", maxsplit=1
    )[0]

    assert "(router_headers)" in content
    assert "import router_headers" in router_block
    assert "import secure_headers" not in router_block

    router_headers = content.split("(router_headers) {", maxsplit=1)[1].split(
        "copyparty.hchu.me {", maxsplit=1
    )[0]
    assert "X-Content-Type-Options" not in router_headers
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"' in router_headers
    assert 'X-Frame-Options "DENY"' in router_headers
