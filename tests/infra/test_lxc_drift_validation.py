from tests.helpers import REPO_ROOT


def test_validate_playbook_checks_root_only_lxc_option_drift_without_mutating():
    validate = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml").read_text(encoding="utf-8")

    assert "Validate Proxmox LXC root-only settings" in validate
    assert "pct config" in validate
    assert "pve_lxc_root_options" in validate
    assert "grep -Eq '{{ setting.pattern }}'" in validate

    drift_play = validate.split("- name: Validate Proxmox LXC root-only settings", maxsplit=1)[1].split("- name: Validate edge", maxsplit=1)[0]
    assert "changed_when: false" in drift_play
    assert "pct set" not in drift_play
    assert "pct shutdown" not in drift_play
    assert "pct stop" not in drift_play
