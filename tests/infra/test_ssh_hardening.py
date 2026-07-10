from tests.helpers import REPO_ROOT


HARDENING_LINES = (
    "PasswordAuthentication no",
    "KbdInteractiveAuthentication no",
    "ChallengeResponseAuthentication no",
    "PermitRootLogin prohibit-password",
)


def test_common_debian_hardens_sshd_without_disabling_root_key_login():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "common_debian" / "tasks" / "main.yml").read_text(encoding="utf-8")
    handlers = (REPO_ROOT / "infra" / "ansible" / "roles" / "common_debian" / "handlers" / "main.yml").read_text(encoding="utf-8")

    assert "Configure Debian SSH hardening" in tasks
    assert "/etc/ssh/sshd_config" in tasks
    for line in HARDENING_LINES:
        assert line in tasks
    assert "PermitRootLogin no" not in tasks
    assert "Restart Debian ssh" in handlers
    assert "name: ssh" in handlers
