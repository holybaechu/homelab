from tests.helpers import REPO_ROOT


def test_runbook_documents_four_lxc_target_and_rollback():
    runbook = (REPO_ROOT / "docs/runbooks/docker-compose-migration.md").read_text(encoding="utf-8")
    assert "four LXCs" in runbook
    assert "tailnet" in runbook
    assert "docker_apps" in runbook
    assert "openclaw" in runbook
    assert "ctf-executor" in runbook
    assert "Rollback" in runbook




def test_validate_compose_script_is_tracked_and_ci_invokes_it():
    script = (REPO_ROOT / "scripts/ci/validate-compose.sh").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pytest" not in script
    assert "--syntax-check" not in script
    assert "docker compose" in script
    assert "./scripts/ci/validate-compose.sh" in workflow
