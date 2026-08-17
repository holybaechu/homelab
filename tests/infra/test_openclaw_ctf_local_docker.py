import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_ctf_local_docker"


def read(path):
    return path.read_text(encoding="utf-8")


def test_local_ctf_docker_builds_the_pinned_kali_image_for_the_gateway_host():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    names = [task["name"] for task in tasks]
    dockerfile = read(ROLE / "files/Dockerfile")
    variables = yaml.safe_load(
        read(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml")
    )

    assert variables["openclaw_ctf_image"] == "homelab-openclaw-ctf-kali:1"
    assert variables["openclaw_ctf_image_revision"] == "6"
    assert "camoufox[geoip]==0.5.4" in dockerfile
    assert "Grant only the Gateway service account local Docker access" in names
    assert "Build the pinned local CTF Kali image" in names
    assert "Validate the pinned local Kali image and browser tools" in names


def test_local_ctf_docker_keeps_the_network_and_daemon_hardening():
    tasks = read(ROLE / "tasks/main.yml")
    daemon = yaml.safe_load(read(ROLE / "templates/daemon.json.j2"))
    firewall = read(ROLE / "templates/openclaw-ctf-docker-firewall.sh.j2")

    assert daemon["icc"] is False
    assert daemon["userland-proxy"] is False
    assert daemon["log-opts"] == {"max-file": "3", "max-size": "10m"}
    assert "com.docker.network.bridge.enable_icc=false" in tasks
    for cidr in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ):
        assert cidr in firewall


def test_site_stages_local_docker_before_the_gateway_role():
    site = yaml.safe_load(
        read(REPO_ROOT / "infra/ansible/playbooks/site.yml")
    )
    play = next(
        entry
        for entry in site
        if entry["name"] == "Stage or activate the dedicated native OpenClaw Gateway"
    )
    assert [role["role"] for role in play["roles"]] == [
        "openclaw_ctf_local_docker",
        "openclaw_native",
    ]
