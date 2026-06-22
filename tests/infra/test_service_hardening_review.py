from tests.helpers import REPO_ROOT


def test_qbittorrent_config_update_only_stops_service_when_config_changes():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert "Render qBittorrent config candidate" in tasks
    assert "Compare qBittorrent config candidate" in tasks
    assert "when: qbittorrent_config_compare.rc != 0" in tasks
    assert "Stop qBittorrent before applying changed config" in tasks


def test_root_only_lxc_options_use_graceful_shutdown_before_stop():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "pve_lxc_root_options" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert "pct shutdown" in tasks
    assert "pct stop" in tasks
    assert tasks.index("pct shutdown") < tasks.index("pct stop")


def test_storage_role_only_reconciles_recursive_permissions_when_needed():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "pve_homelab_storage" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert ".homelab-permissions-initialized" in tasks
    assert "homelab_data_reconcile_permissions" in tasks
    assert "permissions_changed=1" in tasks


def test_copyparty_role_uses_plaintext_passwords_and_private_config_mode():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "copyparty" / "tasks" / "main.yml").read_text(encoding="utf-8")
    template = (REPO_ROOT / "infra" / "ansible" / "roles" / "copyparty" / "templates" / "copyparty.conf.j2").read_text(encoding="utf-8")

    assert "copyparty_password_hash_salt" not in tasks
    assert "selectattr('password_hash', 'defined')" in tasks
    assert "user.password" in template
    assert "user.password_hash" not in template
    assert "ah-alg" not in template
    assert "ah-salt" not in template
    assert 'mode: "0600"' in tasks


def test_caddy_config_changes_reload_gracefully():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "caddy" / "tasks" / "main.yml").read_text(encoding="utf-8")
    handlers = (REPO_ROOT / "infra" / "ansible" / "roles" / "caddy" / "handlers" / "main.yml").read_text(encoding="utf-8")

    assert "notify: Reload caddy" in tasks
    assert "caddy reload" in handlers
    assert "Restart caddy" in handlers


def test_qbittorrent_and_hermes_units_use_systemd_sandboxing():
    qbt = (REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "templates" / "qbittorrent.service.j2").read_text(encoding="utf-8")
    hermes = (REPO_ROOT / "infra" / "ansible" / "roles" / "hermes" / "templates" / "hermes-gateway.service.j2").read_text(encoding="utf-8")

    for unit in (qbt, hermes):
        assert "NoNewPrivileges=true" in unit
        assert "PrivateTmp=true" in unit
        assert "ProtectSystem=full" in unit
        assert "RestrictSUIDSGID=true" in unit
        assert "ReadWritePaths=" in unit


def test_tailscale_up_is_not_reported_changed_unconditionally():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "tailscale_gateway" / "tasks" / "main.yml").read_text(encoding="utf-8")

    join_task = tasks.split("- name: Join Tailscale when auth key is supplied", maxsplit=1)[1]
    assert "changed_when: false" in join_task
    assert "changed_when: true" not in join_task


def test_shared_readonly_mount_is_readonly_at_lxc_boundary():
    all_vars = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml").read_text(encoding="utf-8")

    assert "mp=/srv/shared-readonly,ro=1" in all_vars
