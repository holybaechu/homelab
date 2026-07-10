from tests.helpers import REPO_ROOT


def test_retired_native_service_roles_and_scripts_are_removed():
    for path in (
        "apps/edge/Caddyfile",
        "apps/downloads/scripts/proton_natpmp_qbt.py",
        "apps/downloads/scripts/proton_select_wireguard_server.py",
        "infra/ansible/roles/caddy/tasks/main.yml",
        "infra/ansible/roles/downloads_vpn/tasks/main.yml",
        "infra/ansible/roles/qbittorrent/tasks/main.yml",
    ):
        assert not (REPO_ROOT / path).exists()


def test_common_test_helpers_remove_repeated_repo_root_boilerplate():
    assert (REPO_ROOT / "tests/helpers.py").exists()
    repeated = []
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        if "Path(__file__).resolve().parents" + "[2]" in path.read_text(encoding="utf-8"):
            repeated.append(str(path.relative_to(REPO_ROOT)))
    assert repeated == []
