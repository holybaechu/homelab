import importlib.util
import os

import pytest
import yaml

from tests.helpers import REPO_ROOT


SCRIPT = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "openclaw_native"
    / "files"
    / "openclaw_skill_sync.py"
)
ROLE = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "openclaw_native"
    / "tasks"
    / "main.yml"
)
SERVICE = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "openclaw_native"
    / "templates"
    / "openclaw-skill-sync.service.j2"
)


def load_sync():
    spec = importlib.util.spec_from_file_location("openclaw_skill_sync", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_collector_validates_and_mirrors_both_managed_skill_roots(tmp_path):
    sync = load_sync()
    uid = (tmp_path.stat().st_uid)
    token = "github-token-" + "x" * 24
    main = tmp_path / "live-main"
    ctf = tmp_path / "live-ctf"
    (main / "adaptive-main").mkdir(parents=True)
    (ctf / "campaign-helper" / "references").mkdir(parents=True)
    (main / "adaptive-main" / "SKILL.md").write_text("main\n", encoding="utf-8")
    (ctf / "campaign-helper" / "SKILL.md").write_text("ctf\n", encoding="utf-8")
    (ctf / "campaign-helper" / "references" / "notes.md").write_text(
        "notes\n", encoding="utf-8"
    )

    collected = sync.collect_roots(
        {
            "workspaces/main/skills": main,
            "workspaces/ctf/skills": ctf,
        },
        token,
        uid,
    )
    repo = tmp_path / "repo"
    (repo / "workspaces/main/skills/stale").mkdir(parents=True)
    (repo / "workspaces/main/skills/stale/SKILL.md").write_text(
        "stale\n", encoding="utf-8"
    )
    sync.apply_collected(repo, collected)

    assert not (repo / "workspaces/main/skills/stale").exists()
    assert (repo / "workspaces/main/skills/adaptive-main/SKILL.md").read_text() == "main\n"
    assert (repo / "workspaces/ctf/skills/campaign-helper/SKILL.md").read_text() == "ctf\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_collector_rejects_credentials_in_promoted_skill(tmp_path):
    sync = load_sync()
    root = tmp_path / "skills"
    skill = root / "bad-skill"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(
        "github_pat_" + "x" * 30, encoding="utf-8"
    )

    with pytest.raises(sync.SyncError, match="resembles a credential"):
        sync.collect_roots(
            {"workspaces/main/skills": root},
            "github-token-" + "z" * 24,
            root.stat().st_uid,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_collector_rejects_symlinked_skill_content(tmp_path):
    sync = load_sync()
    root = tmp_path / "skills"
    skill = root / "linked-skill"
    skill.mkdir(parents=True)
    target = tmp_path / "outside.md"
    target.write_text("outside\n", encoding="utf-8")
    try:
        skill.joinpath("SKILL.md").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(sync.SyncError, match="unsupported skill file type"):
        sync.collect_roots(
            {"workspaces/main/skills": root},
            "github-token-" + "z" * 24,
            root.stat().st_uid,
        )


def test_role_keeps_promotion_outside_gateway_and_auto_merges_checked_prs():
    role = ROLE.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    yaml.safe_load(role)

    assert "User={{ openclaw_skill_sync_user }}" in service
    assert "LoadCredential=github_token:{{ openclaw_skill_sync_github_token_path }}" in service
    assert "ReadOnlyPaths={{ openclaw_workspace_root }}/skills" in service
    assert "ReadOnlyPaths={{ openclaw_ctf_workspace_root }}/skills" in service
    assert "ReadWritePaths={{ openclaw_skill_sync_state_root }}" in service
    assert "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN" not in (
        REPO_ROOT
        / "infra/ansible/roles/openclaw_native/templates/openclaw-gateway.service.j2"
    ).read_text(encoding="utf-8")
    assert 'f"{api}/actions/runs?{query}"' in script
    assert '"event": "pull_request"' in script
    assert '"head_sha": sha' in script
    assert '"merge_method": "squash"' in script
    assert '"event_type": "openclaw-promoted"' in script
    assert '"client_payload": {"commit": commit}' in script
    assert "last-dispatched-commit" in script
    assert '["git", "push", "origin", f"HEAD:refs/heads/{branch}"]' in script
    assert '["git", "push", "origin", "main"]' not in script
    assert role.index("Probe the native OpenClaw Gateway readiness endpoint") < role.index(
        "Enable autonomous OpenClaw skill promotion after Gateway readiness"
    )


def test_role_syncs_canonical_setup_without_persisting_a_remote_or_token():
    tasks = yaml.safe_load(ROLE.read_text(encoding="utf-8"))
    task = next(
        item
        for item in tasks
        if item.get("name")
        == "Synchronize the protected private OpenClaw checkout from canonical main"
    )
    shell = task["ansible.builtin.shell"]
    environment = task["environment"]

    assert "git -C \"$setup\" fetch --no-tags" in shell
    assert "https://github.com/{{ openclaw_skill_sync_repository }}.git" in shell
    assert "git -C \"$setup\" reset --hard \"$fetched\"" in shell
    assert "openclaw_setup_expected_commit" in shell
    assert "test \"$fetched\" = \"$expected\"" in shell
    assert "git -C \"$setup\" clean -fdx" in shell
    assert 'test -z "$(git -C "$setup" remote)"' in shell
    assert "systemctl stop openclaw-gateway.service" in shell
    assert "openclaw_skill_sync_github_token" not in shell
    assert environment["GIT_ASKPASS"] == "{{ openclaw_skill_sync_askpass_path }}"
    assert environment["OPENCLAW_SKILL_SYNC_GITHUB_TOKEN_FILE"] == (
        "{{ openclaw_skill_sync_github_token_path }}"
    )
    assert task["no_log"] is True
    names = [item.get("name") for item in tasks]
    assert names.index(task["name"]) < names.index(
        "Inspect the transferred private OpenClaw repository"
    )
