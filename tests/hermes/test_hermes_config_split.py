import pytest
import yaml

from tests.helpers import REPO_ROOT


HERMES_CONFIG_ROOT = REPO_ROOT.parent / "hermes-config"


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_homelab_hermes_config_behavior_state_does_not_vendor_artifacts():
    role_files = REPO_ROOT / "infra/ansible/roles/hermes/files"
    role_templates = REPO_ROOT / "infra/ansible/roles/hermes/templates"

    assert not (role_files / "plugins/newrrow-browser-login").exists()
    assert not (role_files / "skills/newrrow-points-automation").exists()
    assert not (role_templates / "hermes-kanban-diagnostics.sh.j2").exists()


def test_homelab_hermes_config_declarative_state_has_no_local_drift():
    if not HERMES_CONFIG_ROOT.exists():
        pytest.skip("sibling hermes-config checkout is not available in this test environment")

    group_vars = read_yaml(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    assert group_vars["hermes_kanban"] == read_yaml(HERMES_CONFIG_ROOT / "kanban/settings.yaml")
    assert group_vars["hermes_kanban_boards"] == read_yaml(HERMES_CONFIG_ROOT / "kanban/boards.yaml")
    assert group_vars["hermes_profile_bundled_skill_sources"] == read_yaml(
        HERMES_CONFIG_ROOT / "config/profile-required-skills.yaml"
    )
    assert group_vars["hermes_profiles"] == read_yaml(HERMES_CONFIG_ROOT / "profiles/profiles.yaml")

    default_config = read_yaml(HERMES_CONFIG_ROOT / "config/default/config.yaml")
    assert default_config["agent"]["max_turns"] == 1_000_000
    assert default_config["delegation"]["max_iterations"] == 1_000_000

    cron_names = {
        path.name: read_yaml(path).get("name")
        for path in (HERMES_CONFIG_ROOT / "cron").glob("*.yaml")
    }
    assert cron_names["homelab-kanban-daily-diagnostics.yaml"] == "homelab-kanban-daily-diagnostics"
    assert cron_names["newrrow-daily-points.yaml"] == "Daily Newrrow points automation at 12:00 AM PDT"
