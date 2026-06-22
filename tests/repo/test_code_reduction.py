from tests.helpers import REPO_ROOT


def test_openrc_loop_template_is_shared_by_ddns_and_adguard_acme():
    template = REPO_ROOT / "infra" / "ansible" / "templates" / "openrc-loop.sh.j2"
    ddns = (REPO_ROOT / "infra" / "ansible" / "roles" / "ddns" / "tasks" / "main.yml").read_text(encoding="utf-8")
    acme = (REPO_ROOT / "infra" / "ansible" / "roles" / "adguard_acme" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert template.exists()
    assert "openrc-loop.sh.j2" in ddns
    assert "openrc-loop.sh.j2" in acme
    assert "while true; do" not in ddns
    assert "while true; do" not in acme


def test_common_test_helpers_removed_repeated_repo_root_boilerplate():
    helper = REPO_ROOT / "tests" / "helpers.py"
    assert helper.exists()

    repeated = []
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        old_boilerplate = "Path(__file__).resolve().parents" + "[2]"
        if old_boilerplate in text:
            repeated.append(str(path.relative_to(REPO_ROOT)))
    assert repeated == []


def test_openrc_loop_template_shell_quotes_environment_values():
    template = (REPO_ROOT / "infra" / "ansible" / "templates" / "openrc-loop.sh.j2").read_text(encoding="utf-8")

    assert "{{ item.value | quote }}" in template
