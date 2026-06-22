from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[2]


def render_copyparty_config(copyparty_users):
    env = Environment(
        loader=FileSystemLoader(
            REPO_ROOT / "infra" / "ansible" / "roles" / "copyparty" / "templates"
        ),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("copyparty.conf.j2")
    return template.render(
        copyparty_users=copyparty_users,
        copyparty_listen_port=3923,
    )


def test_copyparty_config_renders_only_supplied_accounts():
    rendered = render_copyparty_config(
        [{"name": "holybaechu", "password": "example-password"}]
    )

    assert "[accounts]" in rendered
    assert "holybaechu: example-password" in rendered
    accounts_section = rendered.split("[global]", 1)[0]
    assert "siregon72" not in rendered
    assert "ezmin1104" not in rendered
    assert "bjh_deepfake_contest:" not in accounts_section
    assert "r: holybaechu" in rendered
    assert "A: holybaechu" in rendered
    assert "[/music]" not in rendered
    assert "/srv/music" not in rendered
    assert "[/bjh_deepfake_contest]" not in rendered
    assert "/srv/bjh_deepfake_contest" not in rendered


def test_copyparty_secret_users_get_shared_read_only_access():
    rendered = render_copyparty_config(
        [
            {"name": "holybaechu", "password": "owner-password"},
            {"name": "siregon72", "password": "guest-password"},
            {"name": "ezmin1104", "password": "guest-password"},
            {"name": "sieon", "password": "guest-password"},
        ]
    )

    accounts_section = rendered.split("[global]", 1)[0]
    shared_section = rendered.split("[/shared-readonly]", 1)[1].split("[/downloads]", 1)[0]
    downloads_section = rendered.split("[/downloads]", 1)[1]

    assert "siregon72: guest-password" in accounts_section
    assert "ezmin1104: guest-password" in accounts_section
    assert "sieon: guest-password" in accounts_section
    assert "\n  usernames\n" in rendered
    assert "r: holybaechu, siregon72, ezmin1104, sieon" in shared_section
    assert "A: holybaechu" in shared_section
    assert "siregon72" not in downloads_section
    assert "ezmin1104" not in downloads_section
    assert "sieon" not in downloads_section
    assert "A: holybaechu, siregon72" not in rendered
    assert "A: holybaechu, ezmin1104" not in rendered
    assert "A: holybaechu, sieon" not in rendered
