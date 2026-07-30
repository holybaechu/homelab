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
    assert variables.index("name: media") < variables.index("name: hermes")


def test_compose_role_removes_retired_backup_project_and_volumes():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    variables = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml").read_text(encoding="utf-8")
    active_projects = variables.split("\ndocker_compose_projects:", 1)[1]

    assert "docker compose down --volumes --remove-orphans" in tasks
    assert "retired_docker_compose_projects" in tasks
    assert "name: backup" in variables
    assert "name: backup" not in active_projects


def test_retired_data_cleanup_is_not_attempted_inside_unprivileged_lxc():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    variables = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml").read_text(encoding="utf-8")

    assert "retired_docker_data_paths" not in variables
    assert "Validate retired Docker data paths" not in tasks
    assert "Remove retired Docker data paths" not in tasks
