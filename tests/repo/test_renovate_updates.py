import json

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_operational_dependencies_do_not_use_latest_aliases():
    paths = [
        ".github/workflows/ci.yml",
        ".github/workflows/cd.yml",
        "scripts/ci/install-tools.sh",
        *[str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "apps/compose").rglob("compose.yml")],
        "apps/compose/hermes/Dockerfile",
    ]
    contents = "\n".join(read(path) for path in paths)
    assert "ubuntu-latest" not in contents
    assert "version: latest" not in contents
    assert ":latest" not in contents


def test_renovate_uses_builtin_compose_and_dockerfile_managers():
    config = json.loads(read("renovate.json"))
    assert "config:recommended" in config["extends"]
    assert all(
        "svc_(edge|dns|downloads)" not in pattern
        for manager in config.get("customManagers", [])
        for pattern in manager.get("managerFilePatterns", [])
    )


def test_ansible_install_is_pinned_and_opentofu_lockfile_is_tracked():
    assert "ansible==" in read("requirements-deploy.txt")
    assert "requirements-deploy.txt" in read(".github/workflows/ci.yml")
    assert ".terraform.lock.hcl" not in read(".gitignore")
