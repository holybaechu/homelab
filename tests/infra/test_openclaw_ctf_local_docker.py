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
    assert variables["openclaw_ctf_camoufox_channel"] == "official/prerelease"
    assert "camoufox[geoip]==0.5.4" in dockerfile
    assert "Grant only the Gateway service account local Docker access" in names
    assert "Build the pinned local CTF Kali image" in names
    assert "Validate the pinned local Kali image and browser tools" in names
    assert "Create persistent local CTF package and browser caches" in names
    assert "Fetch the persistent Camoufox browser bundle" in names
    assert "Validate the persistent Camoufox browser bundle" in names
    build = next(
        task for task in tasks if task["name"] == "Build the pinned local CTF Kali image"
    )
    argv = build["ansible.builtin.command"]["argv"]
    assert argv[argv.index("build") + 1 : argv.index("--label")] == [
        "--network",
        "host",
    ]

    fetch = next(
        task
        for task in tasks
        if task["name"] == "Fetch the persistent Camoufox browser bundle"
    )
    fetch_argv = fetch["ansible.builtin.command"]["argv"]
    assert "{{ openclaw_ctf_docker_network }}" in fetch_argv
    assert "{{ openclaw_ctf_uid }}:{{ openclaw_ctf_gid }}" in fetch_argv
    assert "{{ openclaw_ctf_workspace_root }}:/workspace:rw" in fetch_argv
    script = fetch_argv[-1]
    assert "camoufox set '{{ openclaw_ctf_camoufox_channel }}'" in script
    assert "camoufox fetch" in script
    assert ".homelab-ready-{{ openclaw_ctf_image_revision }}" in script
    validate = next(
        task
        for task in tasks
        if task["name"] == "Validate the persistent Camoufox browser bundle"
    )
    validate_argv = validate["ansible.builtin.command"]["argv"]
    assert validate_argv[validate_argv.index("--network") + 1] == "none"
    assert 'assert p.title() == "Camoufox ready"' in validate_argv[-1]


def test_local_ctf_docker_keeps_the_network_and_daemon_hardening():
    tasks = read(ROLE / "tasks/main.yml")
    daemon = yaml.safe_load(read(ROLE / "templates/daemon.json.j2"))
    firewall = read(ROLE / "templates/openclaw-ctf-docker-firewall.sh.j2")
    native_firewall = read(
        REPO_ROOT / "infra/ansible/roles/openclaw_native/templates/nftables.conf.j2"
    )

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
        assert cidr in native_firewall
    assert 'iifname "{{ openclaw_ctf_docker_bridge }}" accept' in native_firewall
    assert "destroy table inet filter" in native_firewall
    assert "flush ruleset" not in native_firewall


def test_validation_proves_public_ctf_egress_and_private_lan_denial():
    validation = read(REPO_ROOT / "infra/ansible/playbooks/validate.yml")

    assert "--network '{{ openclaw_ctf_docker_network }}'" in validation
    assert "curl -fsS --max-time 10 https://example.com" in validation
    assert "curl -sS --max-time 3 -o /dev/null http://{{ openclaw_ip }}" in validation


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
