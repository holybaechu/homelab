import runpy

import pytest

from tests.helpers import REPO_ROOT


RECONCILE = runpy.run_path(
    str(REPO_ROOT / "scripts" / "ci" / "reconcile-arcane.py")
)
DEPLOY = runpy.run_path(
    str(REPO_ROOT / "scripts" / "ci" / "deploy-with-arcane.py")
)


def test_reconcile_project_specs_are_safe_and_never_include_arcane():
    parse = RECONCILE["parse_project_specs"]

    assert parse(
        [
            "platform=apps/compose/platform/compose.yml",
            "media=apps/compose/media/compose.yml",
        ]
    ) == [
        ("platform", "apps/compose/platform/compose.yml"),
        ("media", "apps/compose/media/compose.yml"),
    ]

    with pytest.raises(ValueError, match="must not Git-sync"):
        parse(["arcane=apps/compose/arcane/compose.yml"])
    with pytest.raises(ValueError, match="safe relative"):
        parse(["media=../media/compose.yml"])
    with pytest.raises(ValueError, match="duplicate Arcane project"):
        parse(
            [
                "media=apps/compose/media/compose.yml",
                "media=apps/compose/media/other.yml",
            ]
        )


def test_reconcile_uses_narrow_role_and_disables_autonomous_work():
    assert RECONCILE["DEPLOY_ROLE_PERMISSIONS"] == [
        "gitops:list",
        "gitops:read",
        "gitops:sync",
    ]
    settings = RECONCILE["MANAGED_SETTINGS"]
    assert settings["autoUpdate"] == "false"
    assert settings["pollingEnabled"] == "false"
    assert settings["scheduledPruneEnabled"] == "false"
    assert settings["autoHealEnabled"] == "false"
    assert settings["lifecycleEnabled"] == "false"
    assert settings["vulnerabilityScanEnabled"] == "false"


def test_deployment_moves_platform_last_without_reordering_other_projects():
    order = DEPLOY["deployment_order"]

    assert order(["platform", "media"]) == ["media", "platform"]
    assert order(["media", "platform"]) == ["media", "platform"]
    assert order(["platform", "media", "code", "openclaw"]) == [
        "media",
        "code",
        "openclaw",
        "platform",
    ]


def test_sync_poll_waits_for_a_fresh_exact_commit_after_transient_proxy_loss():
    expected = "a" * 40
    transient_error = DEPLOY["ArcaneTransientError"]

    class FakeClient:
        request_timeout = 0.1

        def __init__(self):
            self.responses = iter(
                [
                    transient_error("proxy restarting"),
                    {
                        "success": True,
                        "data": {
                            "lastSyncAt": "old",
                            "lastSyncStatus": "success",
                            "lastSyncCommit": "b" * 40,
                        },
                    },
                    {
                        "success": True,
                        "data": {
                            "lastSyncAt": "new",
                            "lastSyncStatus": "success",
                            "lastSyncCommit": expected,
                            "projectId": "project-media",
                        },
                    },
                ]
            )

        def request(self, *_args, **_kwargs):
            response = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return response

    result = DEPLOY["poll_synced_commit"](
        FakeClient(),
        environment_id="0",
        sync_id="sync-media",
        project_name="media",
        previous_sync_at="old",
        expected_commit=expected,
        timeout=1,
        interval=0.001,
    )

    assert result["projectId"] == "project-media"


class RecordingClient:
    def __init__(self):
        self.requests = []

    def request(self, method, path, *_args, **_kwargs):
        self.requests.append((method, path))
        return None


def retired_sync(**overrides):
    sync = {
        "id": "sync-hermes",
        "name": "hermes",
        "repositoryId": "repo-homelab",
        "targetType": "project",
        "projectName": "hermes",
        "composePath": "apps/compose/hermes/compose.yml",
    }
    sync.update(overrides)
    return sync


def retire_hermes(client, syncs):
    return RECONCILE["retire_sync"](
        client,
        environment_id="0",
        existing_syncs=syncs,
        name="hermes",
        compose_path="apps/compose/hermes/compose.yml",
        repository_id="repo-homelab",
    )


def test_retired_sync_absence_is_an_idempotent_noop():
    client = RecordingClient()

    assert retire_hermes(client, []) is False
    assert client.requests == []


def test_retired_sync_is_deleted_only_after_exact_identity_match():
    client = RecordingClient()
    syncs = [retired_sync()]

    assert retire_hermes(client, syncs) is True
    assert client.requests == [
        ("DELETE", "environments/0/gitops-syncs/sync-hermes")
    ]
    assert syncs == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repositoryId", "repo-someone-else"),
        ("targetType", "stack"),
        ("projectName", "not-hermes"),
        ("composePath", "apps/compose/platform/compose.yml"),
    ],
)
def test_retired_sync_identity_mismatch_fails_closed_without_delete(field, value):
    client = RecordingClient()
    sync = retired_sync(**{field: value})
    syncs = [sync]

    with pytest.raises(RECONCILE["ArcaneError"], match="refusing to delete"):
        retire_hermes(client, syncs)

    assert client.requests == []
    assert syncs == [sync]


def test_duplicate_retired_sync_names_fail_closed_without_delete():
    client = RecordingClient()
    syncs = [retired_sync(id="sync-one"), retired_sync(id="sync-two")]

    with pytest.raises(RECONCILE["ArcaneError"], match="multiple GitOps sync"):
        retire_hermes(client, syncs)

    assert client.requests == []


def test_reconcile_rejects_desired_and_retired_project_overlap(monkeypatch):
    parser = RECONCILE["build_parser"]()
    args = parser.parse_args(
        [
            "--base-url",
            "https://arcane.example",
            "--repository-name",
            "homelab",
            "--repository-url",
            "https://github.com/example/homelab.git",
            "--branch",
            "arcane-deploy",
            "--role-name",
            "github-deploy",
            "--credential-name",
            "github-main",
            "--issuer",
            "https://token.actions.githubusercontent.com",
            "--audience",
            "https://arcane.example",
            "--subject",
            "repo:example/homelab:environment:prod",
            "--project",
            "media=apps/compose/media/compose.yml",
            "--retired-project",
            "media=apps/compose/media/compose.yml",
        ]
    )
    monkeypatch.setenv("ARCANE_ADMIN_STATIC_API_KEY", "not-printed")

    with pytest.raises(ValueError, match="both managed and retired: media"):
        RECONCILE["run"](args)


def test_commit_gate_rejects_a_fresh_but_wrong_revision():
    assert_commit = DEPLOY["assert_expected_commit"]
    arcane_error = DEPLOY["ArcaneError"]

    with pytest.raises(arcane_error, match="expected"):
        assert_commit(
            {"lastSyncStatus": "success", "lastSyncCommit": "b" * 40},
            "a" * 40,
            "media",
        )


def test_github_oidc_url_replaces_existing_audience():
    url = DEPLOY["github_oidc_request_url"](
        "https://pipelines.actions.githubusercontent.com/token?job=1&audience=old",
        "https://arcane.home.hchu.me",
    )

    assert "job=1" in url
    assert "audience=old" not in url
    assert "audience=https%3A%2F%2Farcane.home.hchu.me" in url
