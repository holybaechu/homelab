import yaml

from tests.helpers import REPO_ROOT


PACKAGE = REPO_ROOT / "apps" / "compose" / "homelab"


def test_compose_uses_only_package_relative_generated_secret_files():
    model = yaml.safe_load((PACKAGE / "compose.yml").read_text(encoding="utf-8"))
    secret_inputs = {
        path
        for service in model["services"].values()
        for path in service.get("env_file", [])
    }
    assert secret_inputs == {
        "./.secrets/traefik.env",
        "./.secrets/cloudflare-ddns.env",
    }
    assert "/etc/homelab/secrets" not in (PACKAGE / "compose.yml").read_text(
        encoding="utf-8"
    )


def test_public_package_contains_no_recognizable_raw_credentials():
    forbidden = ("BEGIN PRIVATE KEY", "xoxb-", "ghp_", "sk-")
    for path in PACKAGE.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert all(marker.encode() not in content for marker in forbidden), path
