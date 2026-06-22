from tests.helpers import REPO_ROOT


def test_copyparty_openrc_prepares_logs_and_state_home():
    template = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "copyparty"
        / "templates"
        / "copyparty.openrc.j2"
    ).read_text(encoding="utf-8")

    assert 'HOME="{{ copyparty_state_dir }}"' in template
    assert 'XDG_CONFIG_HOME="{{ copyparty_state_dir }}/config"' in template
    assert "export HOME XDG_CONFIG_HOME" in template
    assert "checkpath --file --owner {{ service_user }}:{{ service_group }} --mode 0640 /var/log/copyparty.log" in template
    assert "checkpath --file --owner {{ service_user }}:{{ service_group }} --mode 0640 /var/log/copyparty.err" in template
