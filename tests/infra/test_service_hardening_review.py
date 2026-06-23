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


def test_qbittorrent_can_write_public_copyparty_share_for_seeding():
    all_vars = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml").read_text(encoding="utf-8")
    downloads_vars = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "svc_downloads.yml").read_text(encoding="utf-8")
    qbt_unit = (REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "templates" / "qbittorrent.service.j2").read_text(encoding="utf-8")
    storage_tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "pve_homelab_storage" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert "mp=/public,ro=1" not in all_vars
    assert "mp=/public" in all_vars
    assert "copyparty_public_mount_path: /public" in downloads_vars
    assert "{{ copyparty_public_mount_path }}" in qbt_unit
    assert "setfacl -R -m" in storage_tasks
    assert "homelab_container_uid_offset + downloads_service_uid" in storage_tasks
    assert '"${mount_path}/copyparty/public"' in storage_tasks


def test_downloads_routes_only_qbittorrent_through_vpn():
    site = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "site.yml").read_text(encoding="utf-8")
    downloads_vars = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "svc_downloads.yml").read_text(encoding="utf-8")
    common_debian = (REPO_ROOT / "infra" / "ansible" / "roles" / "common_debian" / "tasks" / "main.yml").read_text(encoding="utf-8")
    qbt_tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "tasks" / "main.yml").read_text(encoding="utf-8")
    qbt_config = (REPO_ROOT / "infra" / "ansible" / "roles" / "qbittorrent" / "templates" / "qBittorrent.conf.j2").read_text(encoding="utf-8")
    nftables = (REPO_ROOT / "infra" / "ansible" / "roles" / "downloads_vpn" / "templates" / "nftables.conf.j2").read_text(encoding="utf-8")
    wg_config = (REPO_ROOT / "infra" / "ansible" / "roles" / "downloads_vpn" / "templates" / "wg-proton.conf.j2").read_text(encoding="utf-8")
    vpn_tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "downloads_vpn" / "tasks" / "main.yml").read_text(encoding="utf-8")

    downloads_play = site.split("- name: Configure downloads LXC", maxsplit=1)[1].split("- name: Configure files LXC", maxsplit=1)[0]
    assert downloads_play.index("- qbittorrent") < downloads_play.index("- downloads_vpn")

    assert "apt_bypass_vpn" not in downloads_vars
    assert "apt_bypass_vpn" not in common_debian
    assert "Migrate active downloads WireGuard away from host-wide VPN before package installs" in common_debian
    assert common_debian.index("Migrate active downloads WireGuard away from host-wide VPN before package installs") < common_debian.index("Install Debian base packages")
    assert "wg show \"${interface}\" fwmark" in common_debian
    assert "not from all fwmark" in common_debian
    assert "suppress_prefixlength 0" in common_debian
    assert "uidrange \"${qbt_uid}-${qbt_uid}\" lookup \"${table}\"" in common_debian
    assert "proton_wireguard_table: 51820" in downloads_vars
    assert "proton_wireguard_rule_priority: 100" in downloads_vars
    assert "Table = off" in wg_config
    assert "to {{ homelab_lan_cidr }} lookup main" in wg_config
    assert "to {{ homelab_tailscale_cidr }} lookup main" in wg_config
    assert "ip -4 route replace default dev %i table {{ proton_wireguard_table }}" in wg_config
    assert "uidrange {{ qbittorrent_uid }}-{{ qbittorrent_uid }} lookup {{ proton_wireguard_table }}" in wg_config
    assert "{{ proton_natpmp_gateway }}/32 dev %i" in wg_config
    assert "ip -4 rule del priority {{ proton_wireguard_rule_priority }}" in wg_config

    assert "Connection\\Interface={{ proton_wg_interface }}" in qbt_config
    assert "meta skuid {{ qbittorrent_uid }} oifname \"{{ proton_wg_interface }}\" accept" in nftables
    assert "meta skuid {{ qbittorrent_uid }} reject" in nftables
    assert "meta skuid != {{ qbittorrent_uid }} accept" in nftables

    enable_qbt_task = qbt_tasks.split("- name: Enable qBittorrent and NAT-PMP timer", maxsplit=1)[1]
    assert "enabled: true" in enable_qbt_task
    assert "state: started" not in enable_qbt_task

    assert vpn_tasks.index("Enable WireGuard") < vpn_tasks.index("Start qBittorrent and NAT-PMP timer after VPN is up")


def test_tailscale_up_is_not_reported_changed_unconditionally():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "tailscale_gateway" / "tasks" / "main.yml").read_text(encoding="utf-8")

    join_task = tasks.split("- name: Join Tailscale when auth key is supplied", maxsplit=1)[1]
    assert "changed_when: false" in join_task
    assert "changed_when: true" not in join_task


def test_shared_readonly_mount_is_readonly_at_lxc_boundary():
    all_vars = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml").read_text(encoding="utf-8")

    assert "mp=/srv/shared-readonly,ro=1" in all_vars
