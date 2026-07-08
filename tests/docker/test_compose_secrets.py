from tests.helpers import REPO_ROOT


def test_real_compose_env_files_are_not_committed_and_env_examples_are_allowed():
    compose_root = REPO_ROOT / "apps" / "compose"
    committed_envs = [path.relative_to(REPO_ROOT) for path in compose_root.rglob(".env")]
    examples = [path.relative_to(REPO_ROOT) for path in compose_root.rglob(".env.example")]
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert committed_envs == []
    assert examples
    assert "*.env" in gitignore


def test_compose_files_do_not_contain_raw_secret_material():
    forbidden = (
        "PVEAPIToken",
        "BEGIN PRIVATE KEY",
        "END PRIVATE KEY",
        "xoxb-",
        "ghp_",
        "sk-",
    )

    for path in (REPO_ROOT / "apps" / "compose").rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{marker} leaked in {path.relative_to(REPO_ROOT)}"


def test_env_templates_are_rendered_by_ansible_not_committed_as_real_envs():
    template_dir = REPO_ROOT / "infra" / "ansible" / "roles" / "docker_compose_project" / "templates"
    for name in ("traefik.env.j2", "media.env.j2", "game.env.j2", "copyparty.conf.j2"):
        assert (template_dir / name).exists()

    media = (template_dir / "media.env.j2").read_text(encoding="utf-8")
    copyparty = (template_dir / "copyparty.conf.j2").read_text(encoding="utf-8")
    assert "{{ proton_wireguard_private_key" in media
    assert "{{ copyparty_users" in media
    assert "{% for user in copyparty_users %}" in copyparty
    assert "{{ user.name }}: {{ user.password }}" in copyparty
