from tests.helpers import REPO_ROOT


def test_runbook_documents_tailnet_and_hermes_stay_outside_docker():
    runbook = (REPO_ROOT / "docs" / "runbooks" / "docker-compose-migration.md").read_text(encoding="utf-8")

    assert "Tailnet stays outside Docker" in runbook
    assert "Hermes stays outside Docker" in runbook
    assert "/var/run/docker.sock" in runbook
    assert "Do not stop Tailnet or Hermes" in runbook


def test_validate_compose_script_is_tracked_and_ci_invokes_it():
    script = (REPO_ROOT / "scripts" / "ci" / "validate-compose.sh").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "tests/docker" in script
    assert "docker compose" in script
    assert "ansible-playbook" in script
    assert "./scripts/ci/validate-compose.sh" in workflow
