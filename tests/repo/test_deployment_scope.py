import runpy

from tests.helpers import REPO_ROOT


def test_only_known_workload_paths_use_arcane_fast_path():
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


def test_selector_returns_only_changed_projects_in_dependency_order():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )

    assert selector["select_arcane_projects"](
        [
            "apps/compose/platform/compose.yml",
            "apps/compose/media/compose.yml",
            "apps/compose/code/Dockerfile",
        ]
    ) == ["platform", "media", "code"]
    assert "select_arcane_build_projects" not in selector


def test_mixed_empty_and_infrastructure_changes_use_full_path():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    classify_paths = selector["classify_paths"]

    assert classify_paths([]) == "full"
    assert classify_paths(["apps/compose/media/compose.yml", "renovate.json"]) == "full"
    assert classify_paths(["infra/ansible/playbooks/site.yml"]) == "full"
    assert classify_paths(["apps/compose/arcane/compose.yml"]) == "full"
    assert classify_paths(["apps/README.md"]) == "full"
    assert classify_paths(["apps/compose/platform/traefik.yml"]) == "full"
    assert classify_paths(["apps/compose/hermes/compose.yml"]) == "full"


def test_safe_workload_changes_still_use_arcane():
    selector = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )
    classify_paths = selector["classify_paths"]

    assert classify_paths(["apps/compose/platform/dynamic.yml"]) == "arcane"
    assert classify_paths(["apps/compose/media/compose.yml"]) == "arcane"
    assert classify_paths(["apps/compose/code/compose.yml"]) == "arcane"
