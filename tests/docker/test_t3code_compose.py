import yaml

from tests.helpers import REPO_ROOT


IMAGE_ROOT = REPO_ROOT / "apps" / "images" / "t3code"
STACK_ROOT = REPO_ROOT / "apps" / "compose" / "homelab"


def test_t3code_uses_pinned_kali_base_and_supported_node_runtime():
    dockerfile = (IMAGE_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM kalilinux/kali-rolling:latest@sha256:" in dockerfile
    assert "FROM node:24.19.0-bookworm-slim@sha256:" in dockerfile
    assert "ARG T3_VERSION=0.0.28" in dockerfile
    assert 'CMD ["t3", "serve", "--host", "0.0.0.0"]' in dockerfile
    assert "USER 1000:1000" in dockerfile


def test_t3code_is_private_persistent_and_does_not_mount_docker_socket():
    compose = yaml.safe_load((STACK_ROOT / "compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["t3code"]
    labels = set(service["labels"])
    volumes = set(service["volumes"])

    assert "build" not in service
    assert service["image"] == (
        "${T3CODE_IMAGE_REF:?T3CODE_IMAGE_REF must be an exact OCI digest reference}"
    )
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["networks"] == ["proxy"]
    assert "/srv/homelab/docker-apps/t3code/home:/home/t3code:rw" in volumes
    assert (
        "/srv/homelab/docker-apps/t3code/workspaces:/workspace:rw" in volumes
    )
    assert not any("docker.sock" in volume for volume in volumes)
    assert "traefik.http.routers.code.rule=Host(`code.home.hchu.me`)" in labels
    assert (
        "traefik.http.routers.code.middlewares="
        "private-only@file,secure-headers@file"
    ) in labels
    assert "traefik.http.services.code.loadbalancer.server.port=3773" in labels


def test_t3code_is_part_of_the_single_direct_deployment_project():
    variables = yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "inventory"
            / "prod"
            / "group_vars"
            / "svc_docker_apps.yml"
        ).read_text(encoding="utf-8")
    )

    assert [
        (project["name"], project["src"], project["env_template"])
        for project in variables["docker_compose_projects"]
    ] == [("homelab", "apps/compose/homelab", "homelab.env.j2")]
    assert variables["t3code_hostname"] == "code.home.hchu.me"
