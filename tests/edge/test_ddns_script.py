from tests.helpers import REPO_ROOT


def test_ddns_script_exists_and_targets_cloudflare_api():
    script = REPO_ROOT / "apps" / "edge" / "ddns" / "update-cloudflare-ddns.sh"
    content = script.read_text(encoding="utf-8")

    assert "CLOUDFLARE_ZONE_ID" in content
    assert "CLOUDFLARE_DDNS_TOKEN" in content
    assert "DDNS_RECORD_NAMES" in content
    assert "api.cloudflare.com/client/v4/zones" in content
    assert "api.ipify.org" in content


def test_ddns_loop_survives_transient_update_failures():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "ddns"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "loop_command: /usr/local/bin/update-cloudflare-ddns" in tasks
    assert "Cloudflare DDNS update failed; retrying" in tasks
    loop_template = (
        REPO_ROOT / "infra" / "ansible" / "templates" / "openrc-loop.sh.j2"
    ).read_text(encoding="utf-8")
    assert 'loop_interval_seconds: "{{ ddns_interval_seconds }}"' in tasks
    assert "if ! {{ loop_command }}; then" in loop_template
    assert "sleep {{ loop_interval_seconds }}" in loop_template


def test_ddns_script_uses_jq_and_checks_cloudflare_success():
    content = (REPO_ROOT / "apps" / "edge" / "ddns" / "update-cloudflare-ddns.sh").read_text(encoding="utf-8")

    assert "jq -e" in content
    assert ".success == true" in content
    assert "jq -n" in content
    assert "sed -n" not in content
