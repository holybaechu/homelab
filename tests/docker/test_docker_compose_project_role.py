from tests.helpers import REPO_ROOT


def test_compose_role_reconciles_projects_in_declared_order():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    variables = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml").read_text(encoding="utf-8")

    assert "docker compose pull --ignore-buildable" in tasks
    assert "docker compose up -d --build --remove-orphans" in tasks
    assert "config_templates" in tasks
    assert 'dest: "{{ item.dest }}/.env"' in tasks
    assert 'mode: "0600"' in tasks
    assert "no_log: true" in tasks
    assert variables.index("name: platform") < variables.index("name: media")
    assert variables.index("name: media") < variables.index("name: backup")
