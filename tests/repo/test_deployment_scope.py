import runpy
import subprocess

from tests.helpers import REPO_ROOT


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def test_only_current_arcane_workload_paths_use_the_fast_path():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    classify_paths = selector["classify_paths"]

    assert classify_paths(
        [
            "apps/compose/media/compose.yml",
            "apps/compose/platform/compose.yml",
            "apps/compose/code/compose.yml",
        ]
    ) == "arcane"
    assert classify_paths(["apps/compose/openclaw/compose.yml"]) == "full"


def test_selector_returns_only_changed_projects_in_dependency_order():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )

    assert selector["select_arcane_projects"](
        [
            "apps/compose/platform/compose.yml",
            "apps/compose/media/compose.yml",
            "apps/compose/code/Dockerfile",
            "apps/compose/openclaw/compose.yml",
        ]
    ) == ["platform", "media", "code"]
    assert "select_arcane_build_projects" not in selector


def test_mixed_empty_and_infrastructure_changes_use_full_path():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    classify_paths = selector["classify_paths"]

    assert classify_paths([]) == "none"
    assert classify_paths(["apps/compose/media/compose.yml", "renovate.json"]) == "arcane"
    assert classify_paths(["infra/ansible/playbooks/site.yml"]) == "full"
    assert classify_paths(["apps/compose/arcane/compose.yml"]) == "full"
    assert classify_paths(["apps/README.md"]) == "full"
    assert classify_paths(["apps/compose/platform/dynamic/routes.yml"]) == "full"
    assert classify_paths(
        [
            "apps/compose/platform/dynamic/routes.yml",
            "apps/compose/media/compose.yml",
        ]
    ) == "full"
    assert classify_paths(["apps/compose/platform/traefik.yml"]) == "full"
    assert classify_paths(["apps/compose/hermes/compose.yml"]) == "full"


def test_openclaw_and_no_deploy_paths_use_their_smallest_scopes():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    classify_paths = selector["classify_paths"]

    assert classify_paths(["docs/runbooks/openclaw.md"]) == "none"
    assert classify_paths(["tests/infra/test_openclaw_native_role.py"]) == "none"
    assert classify_paths(
        ["infra/ansible/roles/openclaw_native/tasks/main.yml"]
    ) == "openclaw"
    assert classify_paths(
        [
            "infra/ansible/roles/openclaw_native/tasks/main.yml",
            "tests/infra/test_openclaw_native_role.py",
        ]
    ) == "openclaw"
    assert classify_paths(
        [
            "infra/ansible/roles/openclaw_native/tasks/main.yml",
            "apps/compose/platform/compose.yml",
        ]
    ) == "full"


def test_safe_workload_changes_still_use_arcane():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    classify_paths = selector["classify_paths"]

    assert classify_paths(["apps/compose/platform/dynamic/middlewares.yml"]) == "arcane"
    assert classify_paths(["apps/compose/media/compose.yml"]) == "arcane"
    assert classify_paths(["apps/compose/code/compose.yml"]) == "arcane"
    assert classify_paths(["apps/compose/openclaw/compose.yml"]) == "full"
    assert classify_paths(
        ["infra/ansible/roles/openclaw_foundation/tasks/main.yml"]
    ) == "full"


def test_protected_route_rename_cannot_bypass_the_full_deployment(monkeypatch, tmp_path):
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    route = tmp_path / "apps" / "compose" / "platform" / "dynamic" / "routes.yml"
    route.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Deployment Scope Test")
    git(tmp_path, "config", "user.email", "scope@example.invalid")
    route.write_text("http: {}\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    before = git(tmp_path, "rev-parse", "HEAD")
    route.rename(route.with_name("renamed.yml"))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "rename protected route")
    current = git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_BEFORE", before)
    monkeypatch.setenv("GITHUB_SHA", current)

    scope, paths, promoted = selector["deployment_scope"](tmp_path)

    assert scope == "full"
    assert "apps/compose/platform/dynamic/routes.yml" in paths
    assert "apps/compose/platform/dynamic/renamed.yml" in paths
    assert promoted == ""


def test_repository_dispatch_uses_only_a_valid_exact_promoted_commit(monkeypatch):
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "repository_dispatch")
    commit = "a" * 40
    monkeypatch.setenv("OPENCLAW_PROMOTED_COMMIT", commit)
    assert selector["deployment_scope"](REPO_ROOT) == ("openclaw", [], commit)
    monkeypatch.setenv("OPENCLAW_PROMOTED_COMMIT", "main")
    assert selector["deployment_scope"](REPO_ROOT) == ("full", [], "")
