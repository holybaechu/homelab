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


def test_adguard_role_installs_htpasswd_for_password_hashing():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- apache2-utils" in tasks


def test_adguard_role_hashes_plaintext_admin_password():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "adguard_admin_password is defined" in tasks
    assert "Hash AdGuard admin password" in tasks
    assert "htpasswd" in tasks
    assert "adguard_admin_password_hash" in tasks

