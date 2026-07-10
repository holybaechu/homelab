from tests.helpers import REPO_ROOT


def test_backup_covers_qbittorrent_downloads_and_copyparty_read_only():
    compose = (REPO_ROOT / "apps/compose/backup/compose.yml").read_text(encoding="utf-8")
    script = (REPO_ROOT / "apps/compose/backup/backup-loop.sh").read_text(encoding="utf-8")

    for source in ("qbittorrent-config", "downloads", "copyparty", "copyparty-state"):
        assert f"/sources/{source}" in compose
        assert f"/sources/{source}" in script
    assert compose.count(":ro") >= 5
    assert "--keep-daily" in script
    assert "--keep-weekly" in script
    assert "--keep-monthly" in script
    assert "--prune" in script


def test_cd_requires_encrypted_off_host_backup_credentials():
    workflow = (REPO_ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")
    writer = (REPO_ROOT / "scripts/ci/write_ansible_extra_vars.py").read_text(encoding="utf-8")
    for name in (
        "BACKUP_RESTIC_REPOSITORY",
        "BACKUP_RESTIC_PASSWORD",
        "BACKUP_AWS_ACCESS_KEY_ID",
        "BACKUP_AWS_SECRET_ACCESS_KEY",
    ):
        assert name in workflow
        assert name in writer

