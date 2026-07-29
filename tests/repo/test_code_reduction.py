from tests.helpers import REPO_ROOT


def test_retired_native_service_roles_and_scripts_are_removed():
    for path in (
        "apps/edge/Caddyfile",
        "apps/downloads/scripts/proton_natpmp_qbt.py",
        "apps/downloads/scripts/proton_select_wireguard_server.py",
        "infra/ansible/roles/caddy/tasks/main.yml",
        "infra/ansible/roles/downloads_vpn/tasks/main.yml",
        "infra/ansible/roles/qbittorrent/tasks/main.yml",
        "apps/compose/backup/compose.yml",
        "infra/ansible/roles/docker_compose_project/templates/backup.env.j2",
        "infra/ansible/roles/pve_pre_cutover_backup/tasks/main.yml",
        "apps/compose/game/.env.example",
        "apps/compose/game/README.md",
        "apps/compose/game/compose.yml",
        "infra/ansible/roles/docker_compose_project/templates/game.env.j2",
        "infra/ansible/roles/docker_compose_project/templates/velocity.toml.j2",
        "docs/runbooks/minecraft-server.md",
        "tests/docker/test_game_compose.py",
    ):
        assert not (REPO_ROOT / path).exists()


def test_common_test_helpers_remove_repeated_repo_root_boilerplate():
    assert (REPO_ROOT / "tests/helpers.py").exists()
    repeated = []
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        if "Path(__file__).resolve().parents" + "[2]" in path.read_text(encoding="utf-8"):
            repeated.append(str(path.relative_to(REPO_ROOT)))
    assert repeated == []
