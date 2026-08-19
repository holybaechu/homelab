from tests.helpers import REPO_ROOT


def test_real_compose_env_files_are_not_committed():
    compose_root = REPO_ROOT / "apps" / "compose"
    assert list(compose_root.rglob(".env")) == []
    examples = list(compose_root.rglob(".env.example"))
    assert examples
    assert all((path.parent / "compose.yml").is_file() for path in examples)
    assert "*.env" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_ansible_renders_every_secret_environment():
    template_dir = REPO_ROOT / "infra/ansible/roles/docker_compose_project/templates"
    for name in (
        "homelab.env.j2",
        "traefik.env.j2",
        "cloudflare-ddns.env.j2",
        "copyparty.conf.j2",
        "qBittorrent.conf.j2",
        "AdGuardHome.yaml.j2",
    ):
        assert (template_dir / name).exists()

    assert not (template_dir / "hermes.env.j2").exists()
    assert not (template_dir / "platform.env.j2").exists()
    assert not (template_dir / "media.env.j2").exists()
    assert not (template_dir / "t3code.env.j2").exists()

    openclaw_vars = (
        REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"
    ).read_text(encoding="utf-8")
    assert "openclaw_gateway_token_path" in openclaw_vars
    assert "openclaw_discord_bot_token_path" in openclaw_vars


def test_compose_files_do_not_contain_raw_secret_material():
    forbidden = ("BEGIN PRIVATE KEY", "xoxb-", "ghp_", "sk-")
    for path in (REPO_ROOT / "apps/compose").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert all(marker not in text for marker in forbidden)
