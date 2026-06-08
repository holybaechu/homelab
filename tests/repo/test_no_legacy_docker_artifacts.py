from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_legacy_docker_artifacts_are_not_preserved_in_repo():
    assert not (REPO_ROOT / "legacy" / "docker-stacks").exists()
    assert not (REPO_ROOT / "scripts" / "migration").exists()

