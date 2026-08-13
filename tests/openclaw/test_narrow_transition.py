from pathlib import Path

import yaml

from tests.helpers import REPO_ROOT


PLAYBOOK_ROOT = REPO_ROOT / "infra" / "ansible" / "playbooks"
ROUTE_TASKS = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "openclaw_traefik_route"
    / "tasks"
    / "main.yml"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def task_by_name(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_narrow_bootstrap_resolves_only_the_dedicated_openclaw_lxc():
    playbook = yaml.safe_load(read(PLAYBOOK_ROOT / "bootstrap-openclaw-native.yml"))

    assert [play["hosts"] for play in playbook] == ["pve_hosts", "svc_openclaw"]
    pve_play = playbook[0]
    tasks = pve_play["tasks"]
    assert pve_play["vars"]["openclaw_host"] == (
        "{{ hostvars['openclaw'].ansible_host }}"
    )
    selection = task_by_name(
        tasks, "Select the dedicated OpenClaw root-option declaration"
    )["ansible.builtin.set_fact"]
    assert "selectattr('name', 'equalto', 'openclaw')" in selection[
        "openclaw_lxc_root_option_candidates"
    ]
    assert "selectattr('name', 'equalto', 'openclaw')" in selection[
        "openclaw_lxc_access_candidates"
    ]
    requirement = task_by_name(
        tasks, "Require exactly one dedicated OpenClaw bootstrap declaration"
    )["ansible.builtin.assert"]["that"]
    assert "openclaw_lxc_root_option_candidates | length == 1" in requirement
    assert "openclaw_lxc_access_candidates | length == 1" in requirement
    for identity_guard in (
        "openclaw_lxc_root_option_candidates[0].vmid | int == 118",
        "openclaw_lxc_access_candidates[0].vmid | int == 118",
        "openclaw_lxc_allocation.vmid | int == 118",
        "openclaw_lxc_root_option_candidates[0].name == 'openclaw'",
        "openclaw_lxc_access_candidates[0].name == 'openclaw'",
        "openclaw_lxc_access_candidates[0].os_family == 'debian'",
        "openclaw_lxc_allocation.hostname == 'openclaw'",
        "openclaw_host == '192.168.0.5'",
        "openclaw_lxc_allocation.ip_address == '192.168.0.5/24'",
        "openclaw_lxc_allocation.mac_address == '02:00:00:BA:EC:05'",
    ):
        assert identity_guard in requirement

    by_name = {task["name"]: task for task in tasks}
    read_key = by_name["Read the dedicated OpenClaw SSH host key through Proxmox"]
    assert read_key["ansible.builtin.command"]["argv"] == [
        "pct",
        "exec",
        "{{ openclaw_lxc_allocation.vmid }}",
        "--",
        "/bin/cat",
        "/etc/ssh/ssh_host_ed25519_key.pub",
    ]
    exact_key = by_name[
        "Require an exact 32-byte dedicated OpenClaw ed25519 public-key payload"
    ]
    assert exact_key["delegate_to"] == "localhost"
    exact_key_argv = exact_key["ansible.builtin.command"]["argv"]
    assert "base64.b64decode" in " ".join(exact_key_argv)
    assert "name == b'ssh-ed25519'" in " ".join(exact_key_argv)
    assert "key_size == 32" in " ".join(exact_key_argv)
    assert "len(key) == 32" in " ".join(exact_key_argv)
    assert exact_key_argv[-1] == "{{ openclaw_lxc_host_key.stdout.split()[1] }}"
    trust_key = by_name["Trust only the dedicated OpenClaw SSH host key"]
    assert trust_key["ansible.builtin.known_hosts"]["name"] == "{{ openclaw_host }}"
    assert trust_key["ansible.builtin.known_hosts"]["key"] == (
        "{{ openclaw_host }} {{ openclaw_lxc_host_key.stdout }}"
    )
    assert trust_key["delegate_to"] == "localhost"
    assert tasks.index(exact_key) < tasks.index(trust_key)

    root_role = task_by_name(
        tasks, "Apply root-only settings only to the dedicated OpenClaw LXC"
    )
    access_role = task_by_name(
        tasks, "Install SSH and Python only in the dedicated OpenClaw LXC"
    )
    assert root_role["ansible.builtin.include_role"]["name"] == "pve_lxc_root_options"
    assert root_role["vars"]["pve_lxc_root_options"] == (
        "{{ openclaw_lxc_root_option_candidates }}"
    )
    assert access_role["ansible.builtin.include_role"]["name"] == (
        "pve_lxc_access_bootstrap"
    )
    assert access_role["vars"]["pve_lxc_access_bootstrap"] == (
        "{{ openclaw_lxc_access_candidates }}"
    )

    rendered = read(PLAYBOOK_ROOT / "bootstrap-openclaw-native.yml")
    assert "ssh-keyscan" not in rendered
    for forbidden in (
        "pve_retire_legacy_lxcs",
        "pve_homelab_storage",
        "hosts: debian",
        "svc_docker_apps",
        "svc_tailnet",
    ):
        assert forbidden not in rendered


def test_narrow_stage_never_invokes_broad_docker_or_arcane_roles():
    playbook = yaml.safe_load(read(PLAYBOOK_ROOT / "stage-openclaw-native.yml"))

    assert [play["hosts"] for play in playbook] == [
        "svc_openclaw",
        "svc_docker_apps",
    ]
    assert playbook[0]["roles"] == ["common_debian", "openclaw_native"]
    assert playbook[1]["roles"] == ["openclaw_traefik_route"]
    phase_guard = task_by_name(
        playbook[0]["pre_tasks"],
        "Require the tracked staging phase to remain non-activating",
    )["ansible.builtin.assert"]["that"]
    assert "not (openclaw_native_activate | bool)" in phase_guard
    rendered = read(PLAYBOOK_ROOT / "stage-openclaw-native.yml")
    for forbidden in (
        "docker_engine",
        "docker_compose_project",
        "openclaw_foundation",
        "arcane_manager",
    ):
        assert forbidden not in rendered


def test_every_transition_run_refreshes_openclaw_ssh_trust_through_proxmox():
    workflow = read(REPO_ROOT / ".github" / "workflows" / "cd.yml")
    trust_playbook = yaml.safe_load(
        read(PLAYBOOK_ROOT / "trust-openclaw-native.yml")
    )
    trust_step = workflow.split(
        "- name: Trust dedicated native OpenClaw LXC access", maxsplit=1
    )[1].split("      - name:", maxsplit=1)[0]

    assert "env.OPENCLAW_NATIVE_TRANSITION == 'true'" in trust_step
    assert "infra/ansible/playbooks/trust-openclaw-native.yml" in trust_step
    assert workflow.index("Bootstrap only the dedicated native OpenClaw LXC") < (
        workflow.index("Trust dedicated native OpenClaw LXC access")
    )
    assert workflow.index("Trust dedicated native OpenClaw LXC access") < (
        workflow.index("Recover an interrupted native OpenClaw migration")
    )

    play = trust_playbook[0]
    by_name = {task["name"]: task for task in play["tasks"]}
    assert play["hosts"] == "pve_hosts"
    assert "selectattr('name', 'equalto', 'openclaw')" in play["vars"][
        "openclaw_lxc_candidates"
    ]
    read_key = by_name["Read the dedicated OpenClaw SSH host key through Proxmox"]
    assert read_key["ansible.builtin.command"]["argv"] == [
        "pct",
        "exec",
        "{{ openclaw_lxc.vmid }}",
        "--",
        "cat",
        "/etc/ssh/ssh_host_ed25519_key.pub",
    ]
    assert by_name["Trust the dedicated OpenClaw SSH host key"]["delegate_to"] == (
        "localhost"
    )
    approved = by_name["Require the approved dedicated OpenClaw VMID"][
        "ansible.builtin.assert"
    ]["that"]
    assert "openclaw_lxc.vmid | int == 118" in approved
    assert "openclaw_lxc_allocation.vmid | int == 118" in approved
    assert "openclaw_lxc.name == 'openclaw'" in approved
    assert "openclaw_lxc.os_family == 'debian'" in approved
    assert "openclaw_lxc_allocation.hostname == 'openclaw'" in approved
    assert "openclaw_host == '192.168.0.5'" in approved
    assert "openclaw_lxc_allocation.ip_address == '192.168.0.5/24'" in approved
    assert (
        "openclaw_lxc_allocation.mac_address == '02:00:00:BA:EC:05'" in approved
    )
    exact_key = by_name["Require an exact 32-byte ed25519 public-key payload"]
    assert exact_key["delegate_to"] == "localhost"
    assert "base64.b64decode" in " ".join(
        exact_key["ansible.builtin.command"]["argv"]
    )
    assert "ssh-keyscan" not in read(PLAYBOOK_ROOT / "trust-openclaw-native.yml")


def test_narrow_route_role_can_recreate_only_traefik():
    tasks = yaml.safe_load(read(ROUTE_TASKS))
    recreate = task_by_name(tasks, "Recreate only Traefik for an unapplied runtime contract")
    command = recreate["ansible.builtin.command"]["cmd"]

    assert command == "docker compose up -d --force-recreate --no-deps traefik"
    assert recreate["when"] == "openclaw_traefik_runtime_contract_requires_recreate | bool"

    desired = task_by_name(tasks, "Compute the desired Traefik runtime contract")
    assert "sha256sum compose.yml traefik.yml" in desired["ansible.builtin.shell"]
    assert "routes.yml" not in desired["ansible.builtin.shell"]
    marker = task_by_name(tasks, "Inspect the applied Traefik runtime contract marker")
    assert marker["ansible.builtin.stat"]["follow"] is False
    commit = task_by_name(tasks, "Commit the applied Traefik runtime contract atomically")
    assert commit["ansible.builtin.copy"]["owner"] == "root"
    assert commit["ansible.builtin.copy"]["group"] == "root"
    assert commit["ansible.builtin.copy"]["mode"] == "0600"
    assert commit["ansible.builtin.copy"]["unsafe_writes"] is False
    assert tasks.index(recreate) < tasks.index(task_by_name(tasks, "Wait for the narrowly reconciled Traefik service")) < tasks.index(commit)
    assert all(
        task["name"] != "Remove the superseded Traefik runtime contract marker after health proof"
        for task in tasks
    )

    directory = task_by_name(tasks, "Create the tracked Traefik dynamic configuration directory")
    assert directory["ansible.builtin.file"]["follow"] is False
    assert task_by_name(
        tasks, "Inspect the tracked Traefik dynamic configuration directory"
    )["ansible.builtin.stat"]["follow"] is False
    assert "stat.islnk" in yaml.safe_dump(task_by_name(
        tasks, "Reject a redirected Traefik dynamic configuration directory"
    ))

    rendered = read(ROUTE_TASKS)
    for forbidden in (
        "docker compose pull",
        "docker compose down",
        "--remove-orphans",
        "docker compose up -d --build",
    ):
        assert forbidden not in rendered


def test_narrow_stage_validation_requires_real_tls_and_exact_staged_state():
    validation = read(PLAYBOOK_ROOT / "validate-openclaw-native-stage.yml")

    assert "openclaw-gateway.service" in validation
    assert "systemctl is-active --quiet nftables" in validation
    assert "openclaw_native_cutover_marker_path" in validation
    assert "openclaw.home.hchu.me" in validation
    assert "Resolve the staged OpenClaw hostname through AdGuard" in validation
    assert "docker_apps_ip not in openclaw_native_stage_dns.stdout_lines" in validation
    assert "'200' if openclaw_native_source_stage_marker.stat.exists" in validation
    assert "else '502'" in validation
    assert "--insecure" not in validation
    assert "Validate the committed Traefik runtime contract checkpoint" in validation
    assert "sha256sum compose.yml traefik.yml" in validation


def test_traefik_runtime_contract_retries_every_crash_prefix_without_restarting_for_routes():
    tasks = yaml.safe_load(read(ROUTE_TASKS))
    names = [task["name"] for task in tasks]
    assert names.index("Copy the platform Compose definition last") < names.index(
        "Compute the desired Traefik runtime contract"
    )
    assert names.index("Compute the desired Traefik runtime contract") < names.index(
        "Recreate only Traefik for an unapplied runtime contract"
    )
    assert names.index("Recreate only Traefik for an unapplied runtime contract") < names.index(
        "Wait for the narrowly reconciled Traefik service"
    ) < names.index("Commit the applied Traefik runtime contract atomically")
    rendered = read(ROUTE_TASKS)
    assert "stat.isreg" in rendered
    assert "stat.islnk" in rendered
    assert "Reject an unsafe Traefik runtime contract marker" in rendered
    assert "stat.uid == 0" in rendered
    assert "stat.gid == 0" in rendered
    assert "stat.mode == '0600'" in rendered
    marker_guard = task_by_name(tasks, "Reject an unsafe Traefik runtime contract marker")
    marker_guard_text = " ".join(marker_guard["ansible.builtin.assert"]["that"])
    assert r"^[0-9a-f]{64}\\n$" in marker_guard_text
    reconciliation = task_by_name(
        tasks, "Select durable Traefik runtime contract reconciliation"
    )["ansible.builtin.set_fact"][
        "openclaw_traefik_runtime_contract_requires_recreate"
    ]
    assert "b64decode | trim" in reconciliation
    assert "openclaw_traefik_runtime_contract.stdout | trim" in reconciliation
    assert "+ '\\n'" not in reconciliation
    marker_names = names[
        names.index("Inspect the applied Traefik runtime contract marker") :
        names.index("Remove the superseded single-file Traefik configuration")
    ]
    assert not any(name.startswith("Remove") for name in marker_names)
    assert "openclaw_traefik_routes_copy.changed" not in rendered.split(
        "Select durable Traefik runtime contract reconciliation", 1
    )[1]


def test_transition_workflow_bypasses_every_broad_mutation_and_proves_identities():
    workflow = read(REPO_ROOT / ".github" / "workflows" / "cd.yml")
    topology = read(
        REPO_ROOT / "infra" / "opentofu" / "envs" / "prod" / "containers.auto.tfvars"
    )

    assert 'OPENCLAW_NATIVE_TRANSITION: "true"' in workflow
    assert "OPENCLAW_NATIVE_STAGE_ONLY:" in workflow
    assert "infra/ansible/playbooks/bootstrap-openclaw-native.yml" in workflow
    assert "infra/ansible/playbooks/stage-openclaw-native.yml" in workflow
    assert "infra/ansible/playbooks/validate-openclaw-native-stage.yml" in workflow
    assert "OPENCLAW_LXC_DHCP_RESERVATION_CONFIRMED" not in workflow
    assert 'ip_address       = "192.168.0.5/24"' in topology
    assert 'mac_address      = "02:00:00:BA:EC:05"' in topology
    assert workflow.index("Preflight dedicated OpenClaw LXC allocation") < (
        workflow.index("- name: OpenTofu plan")
    )

    for step_name in (
        "Deploy changed workloads with Arcane",
        "Prepare one-time lowest-ID cutover",
        "Bootstrap Proxmox and LXC access",
        "Deploy services",
        "Validate services",
        "Arm failback and move the subnet route to VMID 111",
        "Prove Proxmox remains reachable after VMID 112 stops",
        "Retire the archived source pair",
    ):
        step = workflow.split(f"- name: {step_name}", maxsplit=1)[1].split(
            "      - name:", maxsplit=1
        )[0]
        assert "env.OPENCLAW_NATIVE_TRANSITION != 'true'" in step

    capture = workflow.split(
        "- name: Capture unaffected transition workload identities", maxsplit=1
    )[1].split("      - name:", maxsplit=1)[0]
    proof = workflow.split(
        "- name: Prove unrelated transition workloads retained their identities",
        maxsplit=1,
    )[1].split("      - name:", maxsplit=1)[0]
    for step in (capture, proof):
        assert "snapshot platform media code openclaw arcane-control" in step
        assert '$1 == "platform" && $2 == "traefik"' in step
        assert '$1 == "openclaw" && $2 == "openclaw-gateway"' in step
        assert '$1 == "arcane-control" && $2 == "arcane"' in step
        assert '$2 == "docker-socket-proxy"' not in step
    assert "always()" in proof
    assert "diff -u" in proof
    assert "{{.Id}}\\t{{.Created}}\\t{{.Image}}\\t{{.Config.Image}}" in workflow
    assert workflow.index("Capture unaffected transition workload identities") < (
        workflow.index("- name: OpenTofu plan")
    )
    assert workflow.index("Validate the narrow native OpenClaw stage") < (
        workflow.index("Transfer and activate native OpenClaw")
    )
    assert workflow.index("Transfer and activate native OpenClaw") < workflow.index(
        "Prove unrelated transition workloads retained their identities"
    )


def test_ci_syntax_checks_every_narrow_transition_playbook():
    ci = read(REPO_ROOT / ".github" / "workflows" / "ci.yml")

    for playbook in (
        "bootstrap-openclaw-native.yml",
        "trust-openclaw-native.yml",
        "stage-openclaw-native.yml",
        "validate-openclaw-native-stage.yml",
    ):
        assert f"infra/ansible/playbooks/{playbook} --syntax-check" in ci
