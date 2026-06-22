from tests.helpers import REPO_ROOT


ACTIVE_CONFIG_PATHS = [
    REPO_ROOT / "apps" / "files" / "copyparty.conf",
    REPO_ROOT
    / "infra"
    / "ansible"
    / "inventory"
    / "prod"
    / "group_vars"
    / "all.yml",
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "copyparty"
    / "tasks"
    / "main.yml",
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "copyparty"
    / "templates"
    / "copyparty.conf.j2",
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "pve_homelab_storage"
    / "tasks"
    / "main.yml",
]


def test_retired_copyparty_shares_are_removed_from_active_config():
    retired_patterns = [
        "bjh_deepfake_contest",
        "/srv/music",
        "copyparty/music",
        "[/music]",
    ]

    for path in ACTIVE_CONFIG_PATHS:
        content = path.read_text(encoding="utf-8")
        for pattern in retired_patterns:
            assert pattern not in content, f"{pattern} still appears in {path}"
