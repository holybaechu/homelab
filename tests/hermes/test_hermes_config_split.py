import json

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

    honcho_policy = json.loads((HERMES_CONFIG_ROOT / "config/honcho.json").read_text(encoding="utf-8"))
    assert honcho_policy["dialecticReasoningLevel"] == "medium"
    assert honcho_policy["dialecticDynamic"] is True
    assert honcho_policy["reasoningLevelCap"] == "high"
    assert honcho_policy["hosts"]["hermes_research"]["reasoningLevelCap"] == "max"

    cron_defs = {
        path.name: read_yaml(path)
        for path in (HERMES_CONFIG_ROOT / "cron").glob("*.yaml")
    }
    assert cron_defs["homelab-kanban-daily-diagnostics.yaml"]["name"] == "homelab-kanban-daily-diagnostics"
    newrrow_cron = cron_defs["newrrow-daily-points.yaml"]
    assert newrrow_cron["name"] == "Daily Newrrow points automation at 12:00 AM PDT"
    assert newrrow_cron["skills"] == ["newrrow-points-automation"]
    assert newrrow_cron["enabled_toolsets"] == ["browser", "todo", "skills"]
    assert newrrow_cron.get("no_agent") in (None, False)


def test_hermes_config_apply_merges_non_secret_honcho_policy():
    apply_template = (REPO_ROOT / "infra/ansible/roles/hermes/templates/hermes-config-apply.py.j2").read_text(encoding="utf-8")

    assert "def apply_honcho_config()" in apply_template
    assert 'CONFIG_DIR / "config/honcho.json"' in apply_template
    assert 'HERMES_HOME / "honcho.json"' in apply_template
    assert "contains_secret_key(desired)" in apply_template
    assert "deep_merge(current, desired)" in apply_template
    assert "live_path.is_symlink()" in apply_template
    assert "Never keep Honcho credentials on a symlink into git-managed state" in apply_template
    assert "apply_honcho_config," in apply_template
    assert "live_path.symlink_to" not in apply_template


def test_hermes_config_apply_preserves_agent_cron_shape():
    apply_template = (REPO_ROOT / "infra/ansible/roles/hermes/templates/hermes-config-apply.py.j2").read_text(encoding="utf-8")

    assert 'desired_no_agent = bool(job.get("no_agent", False))' in apply_template
    assert 'skills=desired_skills' in apply_template
    assert 'enabled_toolsets=desired_toolsets' in apply_template
    assert '"skills": desired_skills' in apply_template
    assert '"enabled_toolsets": desired_toolsets' in apply_template
    assert 'existing.get("skills") not in ([], None)' not in apply_template
    assert 'job.get("no_agent", True)' not in apply_template