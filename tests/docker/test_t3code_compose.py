import yaml

from tests.helpers import REPO_ROOT


CODE_ROOT = REPO_ROOT / "apps" / "compose" / "code"


def test_t3code_uses_pinned_kali_base_and_supported_node_runtime():
    dockerfile = (CODE_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM kalilinux/kali-rolling:latest@sha256:" in dockerfile
    assert "FROM node:24.18.0-bookworm-slim@sha256:" in dockerfile
    assert "ARG T3_VERSION=0.0.28" in dockerfile
    assert 'CMD ["t3", "serve", "--host", "0.0.0.0"]' in dockerfile
    assert "USER 1000:1000" in dockerfile


def test_t3code_is_private_persistent_and_does_not_mount_docker_socket():
    compose = yaml.safe_load((CODE_ROOT / "compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["t3code"]
    labels = set(service["labels"])
    volumes = set(service["volumes"])

    assert service["build"] == {"context": ".", "dockerfile": "Dockerfile"}
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["networks"] == ["proxy"]
    assert "/srv/homelab/docker-apps/t3code/home:/home/t3code:rw" in volumes
    assert "/srv/homelab/workspaces:/workspace:rw" in volumes
    assert not any("docker.sock" in volume for volume in volumes)
    assert "traefik.http.routers.code.rule=Host(`code.home.hchu.me`)" in labels
    assert (
        "traefik.http.routers.code.middlewares="
        "private-only@file,secure-headers@file"
    ) in labels
    assert "traefik.http.services.code.loadbalancer.server.port=3773" in labels


def test_t3code_is_registered_for_ansible_and_arcane():
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

    assert {"name": "code", "compose_path": "apps/compose/code/compose.yml"} in (
        variables["arcane_gitops_projects"]
    )
    assert {
        "name": "code",
        "src": "apps/compose/code",
        "dest": "{{ docker_apps_compose_root }}/code",
        "env_template": "t3code.env.j2",
    } in variables["docker_compose_projects"]
    assert variables["t3code_hostname"] == "code.home.hchu.me"
