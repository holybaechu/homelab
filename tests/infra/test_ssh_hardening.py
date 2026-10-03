from tests.helpers import REPO_ROOT, load_yaml, render_ansible, task_with_module


HARDENING_LINES = (
    "PasswordAuthentication no",
    "KbdInteractiveAuthentication no",
    "ChallengeResponseAuthentication no",
    "PermitRootLogin prohibit-password",
)


def test_common_debian_hardens_sshd_without_disabling_root_key_login():
    role = REPO_ROOT / "infra/ansible/roles/common_debian"
    hardening = task_with_module(load_yaml(role / "tasks/main.yml"),
                                 "ansible.builtin.lineinfile", path="/etc/ssh/sshd_config")
    module = hardening["ansible.builtin.lineinfile"]
    lines = {render_ansible(module["line"], item=item) for item in hardening["loop"]}
    assert set(HARDENING_LINES) <= lines
    assert "PermitRootLogin no" not in lines
    assert module["validate"] == "/usr/sbin/sshd -t -f %s"

    handler = task_with_module(load_yaml(role / "handlers/main.yml"),
                               "ansible.builtin.service", name="ssh", state="restarted")
    notifications = hardening["notify"]
    if isinstance(notifications, str):
        notifications = [notifications]
    assert handler["name"] in notifications
