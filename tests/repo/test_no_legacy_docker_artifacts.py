from tests.helpers import REPO_ROOT


def test_legacy_docker_artifacts_are_not_preserved_in_repo():
    assert not (REPO_ROOT / "legacy" / "docker-stacks").exists()
    assert not (REPO_ROOT / "scripts" / "migration").exists()

