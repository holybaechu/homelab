import subprocess

import pytest

from scripts.ci import select_deployment_components as selector
from tests.helpers import REPO_ROOT


GATEWAY_REF = "ghcr.io/example/openclaw-gateway@sha256:" + "1" * 64
CTF_REF = "ghcr.io/example/openclaw-ctf@sha256:" + "2" * 64


def _release_env(monkeypatch, prefix: str) -> None:
    monkeypatch.setenv(f"{prefix}_CONFIG_COMMIT", "a" * 40)
    monkeypatch.setenv(f"{prefix}_GATEWAY_REF", GATEWAY_REF)
    monkeypatch.setenv(f"{prefix}_CTF_REF", CTF_REF)
    monkeypatch.setenv(f"{prefix}_RUNTIME_SHA256", "3" * 64)
    monkeypatch.setenv(f"{prefix}_CONFIG_SHA256", "4" * 64)


def assert_selection(paths, components, projects=(), builds=()):
    selection = selector.classify_paths(paths)
    assert selection.components == tuple(components)
    assert selection.apps_projects == tuple(projects)
    assert selection.image_builds == tuple(builds)


def test_mixed_changes_union_components_in_canonical_order() -> None:
    assert_selection(
        [
            "infra/opentofu/envs/prod/main.tf",
            "infra/ansible/roles/tailscale_gateway/tasks/main.yml",
            "infra/openclaw/runtime/compose.yml",
            "apps/compose/homelab/compose.yml",
        ],
        selector.COMPONENT_ORDER,
        ("homelab",),
    )


@pytest.mark.parametrize(
    ("path", "components", "projects"),
    [
        ("infra/ansible/inventory/prod/topology.json", ("tofu", "bootstrap"), ()),
        ("infra/opentofu/envs/prod/main.tf", ("tofu", "bootstrap"), ()),
        ("infra/ansible/roles/openclaw_native/tasks/main.yml", ("bootstrap",), ()),
        ("infra/ansible/inventory/prod/group_vars/svc_openclaw.yml", ("bootstrap",), ()),
        ("infra/ansible/roles/docker_compose_project/tasks/main.yml", ("bootstrap", "apps"), ("homelab",)),
        ("infra/ansible/roles/docker_engine/tasks/main.yml", ("bootstrap",), ()),
        ("infra/ansible/roles/tailscale_gateway/tasks/main.yml", ("tailnet",), ()),
        ("infra/openclaw/runtime/compose.yml", ("openclaw",), ()),
        ("apps/compose/homelab/compose.yml", ("apps",), ("homelab",)),
        ("scripts/ci/deploy-compose-via-ssh.sh", ("apps",), ("homelab",)),
        ("scripts/ci/deploy-openclaw-via-ssh.sh", ("openclaw",), ()),
        ("infra/deployment/secrets.json", ("bootstrap", "tailnet", "openclaw", "apps"), ("homelab",)),
    ],
)
def test_runtime_and_host_ownership_is_explicit(path, components, projects) -> None:
    assert_selection([path], components, projects)


def test_image_inputs_build_and_promote_through_their_runtime_lane() -> None:
    assert_selection(
        ["infra/openclaw/gateway/Dockerfile"],
        ("openclaw",), (), ("openclaw_gateway",)
    )
    assert_selection(
        ["infra/openclaw/ctf/Dockerfile"], ("openclaw",), (), ("openclaw_ctf",)
    )
    assert_selection(
        ["apps/images/t3code/Dockerfile"], ("apps",), ("homelab",), ("t3code",)
    )
    assert_selection(
        [
            "infra/openclaw/ctf/Dockerfile",
            "infra/openclaw/gateway/patches/patch.py",
            "apps/images/t3code/Dockerfile",
        ],
        ("openclaw", "apps"),
        ("homelab",),
        selector.IMAGE_BUILD_ORDER,
    )


def test_retired_projects_are_not_deployment_projects_or_openclaw_routes() -> None:
    for path in (
        "apps/compose/platform/compose.yml",
        "apps/compose/media/compose.yml",
        "apps/compose/code/compose.yml",
        "apps/compose/openclaw/compose.yml",
        "infra/ansible/roles/openclaw_foundation/tasks/main.yml",
        "infra/ansible/playbooks/fence-openclaw-docker-before-native.yml",
    ):
        assert_selection([path], ())
    assert selector.APP_PROJECT_ORDER == ("homelab",)


def test_docs_tests_and_validation_helpers_select_nothing() -> None:
    assert_selection(
        [
            "docs/runbooks/openclaw.md",
            "tests/repo/test_openclaw_release.py",
            "scripts/ci/validate-compose.sh",
            "scripts/recovery/compose_stack_cutover.py",
            ".github/workflows/ci.yml",
        ],
        (),
    )
    assert_selection(
        ["scripts/ci/immutable_image_release.py"],
        ("bootstrap", "openclaw", "apps"),
        ("homelab",),
    )


def test_tests_are_validation_only_even_when_they_cover_a_deployer() -> None:
    assert_selection(
        [
            "tests/repo/test_immutable_image_release.py",
            "tests/repo/test_deploy_compose_release.py",
            "tests/repo/test_deploy_openclaw_release.py",
        ],
        (),
    )


def test_app_runtime_inputs_require_preparation_then_one_app_activation() -> None:
    for path in (
        "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml",
        "infra/ansible/roles/docker_compose_project/tasks/main.yml",
    ):
        assert_selection(path.split(), ("bootstrap", "apps"), ("homelab",))


def test_adoption_diff_deletions_have_explicit_empty_tombstones() -> None:
    assert selector.ADOPTION_RETIREMENT_PATHS
    for path in selector.ADOPTION_RETIREMENT_PATHS:
        assert selector.ownership_for_path(path) == selector.owner()

    deleted = subprocess.run(
        ["git", "diff", "--diff-filter=D", "--name-only", "--no-renames", "HEAD", "--"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    strict_deleted = {
        path for path in deleted if path.startswith(selector.STRICT_PREFIXES)
    }
    if strict_deleted:  # The assertion is active while reviewing the adoption worktree.
        assert strict_deleted <= selector.ADOPTION_RETIREMENT_PATHS

    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "HEAD", "--"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    adoption_objects = sorted(set(changed + untracked))
    if adoption_objects:
        # New image/runtime files and every retired path must classify together.
        selector.classify_paths(adoption_objects)


def test_unknown_deployment_paths_fail_closed_with_sorted_list() -> None:
    paths = (
        "apps/compose/new/compose.yml",
        "infra/new-subsystem/file",
        "infra/openclaw/new-runtime/file",
        "infra/ansible/roles/new/tasks/main.yml",
        "scripts/ci/new-deployer.py",
    )
    with pytest.raises(selector.UnownedPathsError) as raised:
        selector.classify_paths(paths)
    assert raised.value.paths == tuple(sorted(paths))
    assert "Add an explicit ownership rule" in str(raised.value)


def test_every_current_strict_tree_file_has_reviewed_ownership() -> None:
    result = subprocess.run(
        [
            "git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
            "apps", "infra", "scripts/ci",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    paths = [path for path in result.stdout.splitlines() if (REPO_ROOT / path).exists()]
    assert paths
    assert [path for path in paths if selector.ownership_for_path(path) is None] == []


def test_push_diff_uses_no_renames_and_apps_is_only_homelab(monkeypatch) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(
            command, 0, "apps/compose/homelab/compose.yml\n", ""
        )

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_DEPLOYMENT_BASE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setattr(selector.subprocess, "run", fake_run)
    selection, paths = selector.selection_for_event(REPO_ROOT)
    assert selection == selector.DeploymentSelection(("apps",), ("homelab",))
    assert paths == ("apps/compose/homelab/compose.yml",)
    assert observed["command"][2:4] == ["--no-renames", "--name-only"]


def test_coalesced_push_diffs_from_last_complete_release_watermark(monkeypatch) -> None:
    observed = {}
    watermark = "a" * 40
    skipped_push = "b" * 40
    current = "c" * 40

    def fake_run(command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            "infra/openclaw/runtime/compose.yml\napps/compose/homelab/compose.yml\n",
            "",
        )

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_DEPLOYMENT_BASE_SHA", watermark)
    monkeypatch.setenv("GITHUB_EVENT_BEFORE", skipped_push)
    monkeypatch.setenv("GITHUB_SHA", current)
    _release_env(monkeypatch, "OPENCLAW_DEFAULT")
    monkeypatch.setattr(selector.subprocess, "run", fake_run)

    selection, paths = selector.selection_for_event(REPO_ROOT)

    assert observed["command"] == [
        "git", "diff", "--no-renames", "--name-only", watermark, current, "--"
    ]
    assert skipped_push not in observed["command"]
    assert selection.components == ("openclaw", "apps")
    assert paths == (
        "infra/openclaw/runtime/compose.yml",
        "apps/compose/homelab/compose.yml",
    )


def test_push_runtime_requires_exact_preapproved_openclaw_release(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_DEPLOYMENT_BASE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setattr(
        selector.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, "infra/openclaw/runtime/compose.yml\n", ""
        ),
    )
    with pytest.raises(selector.SelectionError, match="image promotion"):
        selector.selection_for_event(REPO_ROOT)
    _release_env(monkeypatch, "OPENCLAW_DEFAULT")
    selection, _ = selector.selection_for_event(REPO_ROOT)
    assert selection.components == ("openclaw",)
    assert selection.openclaw_gateway_ref == GATEWAY_REF
    assert selection.openclaw_ctf_ref == CTF_REF


def test_first_push_without_a_watermark_classifies_the_complete_snapshot(
    monkeypatch,
) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            "infra/opentofu/envs/prod/main.tf\napps/compose/homelab/compose.yml\n",
            "",
        )

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_DEPLOYMENT_BASE_SHA", selector.NO_DEPLOYED_REVISION)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setattr(selector.subprocess, "run", fake_run)
    selection, paths = selector.selection_for_event(REPO_ROOT)

    assert observed["command"] == ["git", "ls-tree", "-r", "--name-only", "b" * 40]
    assert selection.components == ("tofu", "bootstrap", "apps")
    assert selection.apps_projects == ("homelab",)
    assert paths == (
        "infra/opentofu/envs/prod/main.tf",
        "apps/compose/homelab/compose.yml",
    )


def test_manual_dispatch_empty_validates_only_and_apps_implies_homelab(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert selector.selection_for_event(REPO_ROOT)[0] == selector.DeploymentSelection(())
    monkeypatch.setenv("MANUAL_COMPONENTS", "apps")
    assert selector.selection_for_event(REPO_ROOT)[0] == selector.DeploymentSelection(
        ("apps",), ("homelab",)
    )


def test_manual_and_promotion_openclaw_require_all_exact_release_fields(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("MANUAL_COMPONENTS", "openclaw")
    with pytest.raises(selector.SelectionError):
        selector.selection_for_event(REPO_ROOT)
    _release_env(monkeypatch, "MANUAL_OPENCLAW")
    manual, _ = selector.selection_for_event(REPO_ROOT)
    assert manual.components == ("openclaw",)
    assert manual.openclaw_setup_commit == "a" * 40

    monkeypatch.setenv("GITHUB_EVENT_NAME", "repository_dispatch")
    _release_env(monkeypatch, "OPENCLAW_PROMOTED")
    promoted, _ = selector.selection_for_event(REPO_ROOT)
    assert promoted.components == ("openclaw",)
    assert promoted.openclaw_gateway_ref == GATEWAY_REF


def test_cli_emits_stable_release_and_build_outputs(monkeypatch, tmp_path) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "repository_dispatch")
    _release_env(monkeypatch, "OPENCLAW_PROMOTED")
    assert selector.main([str(output)]) == 0
    values = dict(
        line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
    )
    assert values == {
        "components": "openclaw",
        "apps_projects": "",
        "openclaw_setup_commit": "a" * 40,
        "openclaw_gateway_ref": GATEWAY_REF,
        "openclaw_ctf_ref": CTF_REF,
        "openclaw_runtime_sha256": "3" * 64,
        "openclaw_config_sha256": "4" * 64,
        "openclaw_builds": "",
        "t3_build": "false",
    }


def test_validation_scope_keeps_common_app_and_openclaw_paths_fast() -> None:
    assert selector.validation_scope(
        [
            "apps/compose/homelab/compose.yml",
            "tests/docker/test_homelab_compose.py",
            "docs/runbooks/docker-compose-migration.md",
        ]
    ) == "apps-model"
    assert selector.validation_scope(
        ["scripts/ci/deploy_compose_release.py"]
    ) == "apps"
    assert selector.validation_scope(
        ["scripts/ci/immutable_image_release.py"]
    ) == "full"
    assert selector.validation_scope(
        [
            "infra/openclaw/runtime/compose.yml",
            "tests/repo/test_deploy_openclaw_release.py",
            "docs/runbooks/openclaw.md",
        ]
    ) == "openclaw"
    assert selector.validation_scope(["infra/opentofu/envs/prod/main.tf"]) == "full"
    assert selector.validation_scope(["docs/runbooks/openclaw.md"]) == "repo"
