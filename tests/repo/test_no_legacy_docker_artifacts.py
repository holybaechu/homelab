from tests.helpers import REPO_ROOT


def test_abandoned_legacy_docker_dump_is_not_preserved_in_repo():
    assert not (REPO_ROOT / "legacy" / "docker-stacks").exists()
    assert not (REPO_ROOT / "scripts" / "migration").exists()


def test_compose_manifests_live_only_under_apps_compose():
    misplaced = []
    for path in REPO_ROOT.rglob("compose.yml"):
        rel = path.relative_to(REPO_ROOT)
        if not rel.parts[:2] == ("apps", "compose"):
            misplaced.append(str(rel))

    for root_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
        assert not (REPO_ROOT / root_name).exists()

    assert misplaced == []
