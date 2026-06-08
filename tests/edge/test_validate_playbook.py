from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_validate_playbook_sources_caddy_environment():
    validate = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml"
    ).read_text(encoding="utf-8")

    assert ". /etc/conf.d/caddy" in validate
    assert "executable: /bin/sh" in validate

