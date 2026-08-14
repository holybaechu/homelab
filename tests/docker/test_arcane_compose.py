import yaml

from tests.helpers import REPO_ROOT


ARCANE_COMPOSE = REPO_ROOT / "apps" / "compose" / "arcane" / "compose.yml"


def load_compose(path=ARCANE_COMPOSE):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_arcane_is_pinned_and_uses_a_private_socket_proxy():
    compose = load_compose()
    arcane = compose["services"]["arcane"]
    proxy = compose["services"]["docker-socket-proxy"]

    assert arcane["image"] == (
        "ghcr.io/getarcaneapp/manager:v2.6.0@sha256:"
        "2b7544497c99d766baaf204ed8d66e555352ef37714aab0b68b5713be0d204d3"
    )
    assert proxy["image"] == (
        "tecnativa/docker-socket-proxy:v0.5.0@sha256:"
        "1f5038b54f06c3e18422902cf00ba21803d1c97805aae032e5e6673d532d3459"
    )
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in proxy["volumes"]
    assert all("docker.sock" not in volume for volume in arcane["volumes"])
    assert "ports" not in proxy
    assert compose["networks"]["socket"]["internal"] is True
    assert proxy["networks"] == ["socket"]
    assert proxy["environment"]["AUTH"] == "0"
    assert proxy["environment"]["SECRETS"] == "0"
    assert proxy["environment"]["BUILD"] == "0"
    assert proxy["environment"]["SESSION"] == "0"


def test_arcane_control_plane_is_separate_persistent_and_private():
    arcane = load_compose()["services"]["arcane"]

    assert arcane["ports"] == ["127.0.0.1:3552:3552"]
    assert "/srv/homelab/docker-apps/arcane/data:/app/data:rw" in arcane["volumes"]
    assert "/opt/homelab-compose:/opt/homelab-compose:rw" in arcane["volumes"]
    assert (
        "/opt/homelab-compose/openclaw-setup:"
        "/opt/homelab-compose/openclaw-setup:ro"
    ) in arcane["volumes"]
    assert "/opt/homelab-control/arcane/secrets:/run/secrets:ro" in arcane["volumes"]
    labels = set(arcane["labels"])
    assert "traefik.http.routers.arcane.rule=Host(`arcane.home.hchu.me`)" in labels
    assert (
        "traefik.http.routers.arcane.middlewares="
        "private-only@file,secure-headers@file"
    ) in labels
    assert arcane["healthcheck"]["test"] == [
        "CMD",
        "./arcane",
        "health",
        "--timeout",
        "2s",
    ]


def test_arcane_never_receives_a_committed_static_admin_key():
    example = (ARCANE_COMPOSE.parent / ".env.example").read_text(encoding="utf-8")

    assert "ADMIN_STATIC_API_KEY" not in example
    assert "ENCRYPTION_KEY_FILE=/run/secrets/encryption_key" in example
    assert "JWT_SECRET_FILE=/run/secrets/jwt_secret" in example


def test_every_managed_container_opts_out_of_arcane_auto_updates():
    for project in ("platform", "media", "code", "openclaw", "arcane"):
        compose = load_compose(
            REPO_ROOT / "apps" / "compose" / project / "compose.yml"
        )
        for service_name, service in compose["services"].items():
            assert "com.getarcaneapp.arcane.updater=false" in service.get(
                "labels", []
            ), f"{project}/{service_name} can bypass Renovate"
