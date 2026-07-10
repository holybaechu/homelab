from tests.helpers import REPO_ROOT


def test_real_compose_env_files_are_not_committed():
    compose_root = REPO_ROOT / "apps" / "compose"
    assert list(compose_root.rglob(".env")) == []
    assert len(list(compose_root.rglob(".env.example"))) == 5
    assert "*.env" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_ansible_renders_every_secret_environment():
    template_dir = REPO_ROOT / "infra/ansible/roles/docker_compose_project/templates"
    for name in (
        "platform.env.j2",
        "media.env.j2",
        "game.env.j2",
        "hermes.env.j2",
        "backup.env.j2",
        "copyparty.conf.j2",
        "qBittorrent.conf.j2",
        "AdGuardHome.yaml.j2",
    ):
        assert (template_dir / name).exists()


def test_compose_files_do_not_contain_raw_secret_material():
    forbidden = ("BEGIN PRIVATE KEY", "xoxb-", "ghp_", "sk-")
    for path in (REPO_ROOT / "apps/compose").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert all(marker not in text for marker in forbidden)
