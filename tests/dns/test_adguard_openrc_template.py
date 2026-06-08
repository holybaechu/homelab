from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_adguard_openrc_prepares_log_files_for_service_user():
    template = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "adguard"
        / "templates"
        / "adguard.openrc.j2"
    ).read_text(encoding="utf-8")

    assert "checkpath --file --owner {{ service_user }}:{{ service_group }} --mode 0640 /var/log/adguardhome.log" in template
    assert "checkpath --file --owner {{ service_user }}:{{ service_group }} --mode 0640 /var/log/adguardhome.err" in template
    assert "checkpath --file --owner {{ service_user }}:{{ service_group }} --mode 0640 {{ adguard_conf_dir }}/AdGuardHome.yaml" in template
    assert "chown -R {{ service_user }}:{{ service_group }} {{ adguard_work_dir }} {{ adguard_conf_dir }}" in template
