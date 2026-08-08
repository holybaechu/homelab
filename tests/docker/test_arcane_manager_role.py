from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_arcane_role_bootstraps_with_an_ephemeral_key_and_removes_it():
    tasks = read("infra/ansible/roles/arcane_manager/tasks/main.yml")
    template = read(
        "infra/ansible/roles/arcane_manager/templates/arcane.env.j2"
    )

    assert "secrets.token_hex(32)" in tasks
    assert "ARCANE_ADMIN_STATIC_API_KEY" in tasks
    assert "reconcile-arcane.py" in tasks
    assert "changed=true" in tasks
    assert "always:" in tasks
    assert tasks.index("Remove the Arcane bootstrap key file") < tasks.index(
        "Force Arcane into its final keyless state"
    )
    assert "--force-recreate arcane" in tasks
    assert "ADMIN_STATIC_API_KEY_FILE=/run/secrets/bootstrap_admin_api_key" in template
    assert "arcane_bootstrap_static_key_enabled" in template
    secret_directory = tasks.split('- path: "{{ arcane_secret_root }}"', 1)[1].split(
        '- path: "{{ arcane_data_root }}"', 1
    )[0]
    persistent_secrets = tasks.split(
        "- name: Install Arcane control-plane secrets", 1
    )[1].split("- name: Copy the Ansible-owned Arcane control project", 1)[0]
    bootstrap_secret = tasks.split(
        "- name: Install the ephemeral Arcane bootstrap key", 1
    )[1].split("- name: Render the bootstrap Arcane environment", 1)[0]
    for section in (secret_directory, persistent_secrets, bootstrap_secret):
        assert "owner: root" in section
        assert 'group: "{{ arcane_gid }}"' in section
    assert 'mode: "0750"' in secret_directory
    assert 'mode: "0640"' in persistent_secrets
    assert 'mode: "0640"' in bootstrap_secret


def test_arcane_role_reconciles_exact_gitops_and_oidc_scope():
    tasks = read("infra/ansible/roles/arcane_manager/tasks/main.yml")
    variables = read(
        "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    )

    assert "Inspect the shared Traefik proxy subnet" in tasks
    assert "Inspect existing workload Compose files before Arcane adoption" in tasks
    assert "Require each Arcane adoption target to have its exact Compose file" in tasks
    assert "Reject alternate root Compose files before Arcane adoption" in tasks
    assert "arcane_alternate_compose_files.matched > 0" in tasks
    assert "      - network" in tasks and "      - inspect" in tasks
    assert "arcane_proxy_network.stdout" in read(
        "infra/ansible/roles/arcane_manager/templates/arcane.env.j2"
    )
    assert "https://github.com/holybaechu/homelab.git" in variables
    assert "arcane_repository_branch: arcane-deploy" in variables
    assert "https://token.actions.githubusercontent.com" in variables
    assert "repo:holybaechu/homelab:environment:prod" in variables
    active_projects = variables.split("arcane_gitops_projects:", 1)[1].split(
        "arcane_retired_gitops_projects:", 1
    )[0]
    retired_projects = variables.split("arcane_retired_gitops_projects:", 1)[1].split(
        "traefik_acme_email:", 1
    )[0]

    assert active_projects.count("compose_path: apps/compose/") == 3
    assert "name: platform" in active_projects
    assert "name: media" in active_projects
    assert "name: code" in active_projects
    assert "name: hermes" not in active_projects
    assert "name: hermes" in retired_projects
    assert "compose_path: apps/compose/hermes/compose.yml" in retired_projects
    assert "--retired-project {{ project.name }}={{ project.compose_path }}" in tasks
    assert "compose_path: apps/compose/arcane/" not in variables


def test_arcane_role_is_separate_and_runs_after_workload_bootstrap():
    site = read("infra/ansible/playbooks/site.yml")
    variables = read(
        "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    )
    workload_section = variables.split("\ndocker_compose_projects:", 1)[1]

    assert site.index("role: docker_compose_project") < site.index(
        "role: arcane_manager"
    )
    assert "name: arcane" not in workload_section
    assert "arcane_control_root: /opt/homelab-control/arcane" in variables
    assert "arcane_data_root: /srv/homelab/docker-apps/arcane/data" in variables


def test_arcane_validation_proves_keyless_proxy_only_runtime():
    validation = read("infra/ansible/playbooks/validate.yml")

    assert "Validate Arcane uses only the private Docker socket proxy" in validation
    assert 'mount["Destination"] == "/var/run/docker.sock"' in validation
    assert 'socket_mounts[0]["RW"]' in validation
    assert "ADMIN_STATIC_API_KEY_FILE=" in validation
    assert "bootstrap_admin_api_key" in validation
    assert "arcane_secret_root" in validation
    assert "arcane_gid" in validation
