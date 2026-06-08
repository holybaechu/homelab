from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_qbittorrent_template_defaults_to_vuetorrent_without_password_hash():
    template = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "qbittorrent"
        / "templates"
        / "qBittorrent.conf.j2"
    ).read_text(encoding="utf-8")

    assert "WebUI\\Username={{ qbittorrent_webui_username }}" in template
    assert 'WebUI\\Password_PBKDF2="{{ qbittorrent_webui_password_pbkdf2 }}"' in template
    assert "WebUI\\AlternativeUIEnabled=true" in template
    assert "WebUI\\RootFolder={{ qbittorrent_vuetorrent_root }}" in template
    assert "qbittorrent_webui_password_hash" not in template


def test_natpmp_updater_defaults_to_configured_qbittorrent_user():
    script = (
        REPO_ROOT / "apps" / "downloads" / "scripts" / "proton_natpmp_qbt.py"
    ).read_text(encoding="utf-8")
    tasks = (
        REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert 'default="holybaechu"' in script
    assert "--qbt-user {{ qbittorrent_webui_username }}" in tasks


def test_downloads_inventory_contains_plaintext_default_but_no_password_hash():
    group_vars = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "downloads.yml"
    ).read_text(encoding="utf-8")
    secrets_readme = (REPO_ROOT / "secrets" / "README.md").read_text(encoding="utf-8")

    assert "qbittorrent_webui_username: holybaechu" in group_vars
    assert "qbittorrent_webui_password:" in group_vars
    assert "qbittorrent_webui_password_hash" not in group_vars
    assert "qbittorrent_webui_password_hash" not in secrets_readme
