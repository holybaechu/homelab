from tests.helpers import REPO_ROOT


def test_compose_project_role_renders_secret_envs_and_uses_remove_orphans():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_compose_project" / "tasks" / "main.yml").read_text(encoding="utf-8")
    site = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "site.yml").read_text(encoding="utf-8")

    assert "{{ playbook_dir }}/../../../{{ item.src }}/" in tasks
    assert "dest: \"{{ item.dest }}/.env\"" in tasks
    assert 'mode: "0600"' in tasks
    assert "no_log: true" in tasks
    assert "docker compose pull" in tasks
    assert "docker compose up -d --remove-orphans" in tasks
    assert "copyparty_config_template" in tasks
    assert "Render media Copyparty config with plaintext password accounts" in tasks
    assert "role: docker_engine" in site
    assert "tags: [docker_apps, docker_engine]" in site
    assert "role: docker_compose_project" in site
    assert "tags: [docker_apps, compose]" in site
