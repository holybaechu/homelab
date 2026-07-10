from tests.helpers import REPO_ROOT


def test_hermes_uses_official_image_persistent_mounts_and_no_docker_socket():
    compose = (REPO_ROOT / "apps/compose/hermes/compose.yml").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "apps/compose/hermes/Dockerfile").read_text(encoding="utf-8")

    assert "FROM nousresearch/hermes-agent:v0.18.2" in dockerfile
    assert "1password-cli" in dockerfile
    assert 'command: ["gateway", "run"]' in compose
    assert "/srv/homelab/hermes/home:/opt/data:rw" in compose
    assert "/srv/homelab/hermes/workspace:/workspace:rw" in compose
    assert "shm_size: 1gb" in compose
    assert "/var/run/docker.sock" not in compose

