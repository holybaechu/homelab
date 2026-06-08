from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_adguard_role_installs_drill_for_dns_validation():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- drill" in tasks

