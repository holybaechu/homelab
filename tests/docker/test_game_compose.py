from tests.helpers import REPO_ROOT


def test_game_compose_uses_itzg_images_and_persistent_bind_mounts():
    compose = (REPO_ROOT / "apps" / "compose" / "game" / "compose.yml").read_text(encoding="utf-8")

    assert "itzg/minecraft-server" in compose
    assert "itzg/mc-proxy" in compose
    assert "/srv/homelab/minecraft/paper:/data:rw" in compose
    assert "/srv/homelab/minecraft/velocity:/server:rw" in compose
    assert 'MEMORY: "${MINECRAFT_MEMORY:-3G}"' in compose


def test_game_compose_exposes_proxy_ports_not_internal_paper_port():
    compose = (REPO_ROOT / "apps" / "compose" / "game" / "compose.yml").read_text(encoding="utf-8")
    paper = compose.split("  paper:", maxsplit=1)[1].split("  velocity:", maxsplit=1)[0]
    velocity = compose.split("  velocity:", maxsplit=1)[1]

    assert "25566:25566" not in compose
    assert "ports:" not in paper
    assert '\"25565:25565/tcp\"' in velocity
    assert '\"19132:19132/udp\"' in velocity
    assert "homelab.optional=true" in compose
