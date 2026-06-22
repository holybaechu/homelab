from tests.helpers import render_role_template


def render_copyparty_config(copyparty_users):
    return render_role_template(
        "copyparty",
        "copyparty.conf.j2",
        copyparty_users=copyparty_users,
        copyparty_listen_port=3923,
    )


def test_copyparty_config_renders_only_hashed_accounts():
    rendered = render_copyparty_config(
        [{"name": "holybaechu", "password_hash": "$2b$example-hash"}]
    )

    assert "[accounts]" in rendered
    assert "holybaechu: $2b$example-hash" in rendered
    assert "example-password" not in rendered
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
            {"name": "holybaechu", "password_hash": "$2b$owner"},
            {"name": "siregon72", "password_hash": "$2b$guest1"},
            {"name": "ezmin1104", "password_hash": "$2b$guest2"},
            {"name": "sieon", "password_hash": "$2b$guest3"},
        ]
    )

    accounts_section = rendered.split("[global]", 1)[0]
    shared_section = rendered.split("[/shared-readonly]", 1)[1].split("[/downloads]", 1)[0]
    downloads_section = rendered.split("[/downloads]", 1)[1]

    assert "siregon72: $2b$guest1" in accounts_section
    assert "ezmin1104: $2b$guest2" in accounts_section
    assert "sieon: $2b$guest3" in accounts_section
    assert "guest-password" not in rendered
    assert "\n  usernames\n" in rendered
    assert "r: holybaechu, siregon72, ezmin1104, sieon" in shared_section
    assert "A: holybaechu" in shared_section
    assert "siregon72" not in downloads_section
    assert "ezmin1104" not in downloads_section
    assert "sieon" not in downloads_section
    assert "A: holybaechu, siregon72" not in rendered
    assert "A: holybaechu, ezmin1104" not in rendered
    assert "A: holybaechu, sieon" not in rendered
