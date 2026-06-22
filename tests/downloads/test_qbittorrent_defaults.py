from tests.helpers import REPO_ROOT


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
    assert "WebUI\\RootFolder={{ qbittorrent_vuetorrent_root_current }}" in template
    assert "qbittorrent_webui_password_hash" not in template


def test_qbittorrent_template_disables_torrent_queueing():
    template = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "qbittorrent"
        / "templates"
        / "qBittorrent.conf.j2"
    ).read_text(encoding="utf-8")

    assert "[BitTorrent]" in template
    assert "Session\\QueueingSystemEnabled=false" in template


def test_natpmp_updater_defaults_to_configured_qbittorrent_user():
    script = (
        REPO_ROOT / "apps" / "downloads" / "scripts" / "proton_natpmp_qbt.py"
    ).read_text(encoding="utf-8")
    tasks = (
        REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert 'default="holybaechu"' in script
    assert "--qbt-user {{ qbittorrent_webui_username }}" in tasks


def test_vuetorrent_archive_extracts_into_versioned_root_and_uses_current_symlink():
    tasks = (
        REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "qbittorrent_vuetorrent_root_current" in tasks
    assert "cp -a" in tasks
    assert "Point qBittorrent at current VueTorrent release" in tasks


def test_qbittorrent_stops_before_rewriting_file_backed_config():
    tasks = (
        REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    stop_task = tasks.index("- name: Stop qBittorrent before applying changed config")
    config_task = tasks.index("- name: Install qBittorrent config")

    assert stop_task < config_task
    assert "Compare qBittorrent config candidate" in tasks
    assert "ansible.builtin.service_facts:" in tasks
    assert "state: stopped" in tasks
    assert "when: qbittorrent_config_compare.rc != 0" in tasks


def test_downloads_inventory_keeps_password_out_of_committed_group_vars():
    group_vars = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "svc_downloads.yml"
    ).read_text(encoding="utf-8")
    secrets_readme = (REPO_ROOT / "secrets" / "README.md").read_text(encoding="utf-8")

    assert "qbittorrent_webui_username: holybaechu" in group_vars
    assert "qbittorrent_webui_password:" not in group_vars
    assert "qbittorrent_webui_password_hash" not in group_vars
    assert "qbittorrent_webui_password" in secrets_readme
    assert "qbittorrent_webui_password_hash" not in secrets_readme


def test_cd_workflow_uses_qbittorrent_password_without_hash_secret():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "QBITTORRENT_WEBUI_PASSWORD:" in workflow
    assert "QBITTORRENT_WEBUI_PASSWORD_HASH" not in workflow
    assert "qbittorrent_webui_password_hash" not in workflow


def test_vuetorrent_install_is_idempotent_with_version_marker():
    tasks = (
        REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "Check VueTorrent version marker" in tasks
    assert ".vuetorrent-{{ qbittorrent_vuetorrent_version }}.installed" in tasks
    assert "when: not qbittorrent_vuetorrent_marker.stat.exists" in tasks
