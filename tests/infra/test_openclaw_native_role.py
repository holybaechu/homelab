import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_native"
VARS = REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"
VALIDATE = REPO_ROOT / "infra/ansible/playbooks/validate.yml"
STAGE_VALIDATE = (
    REPO_ROOT / "infra/ansible/playbooks/validate-openclaw-native-stage.yml"
)


def read(path):
    return path.read_text(encoding="utf-8")


def walk_tasks(tasks):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            yield from walk_tasks(task.get(section, []))


def test_native_openclaw_release_and_node_are_exactly_integrity_pinned():
    variables = yaml.safe_load(read(VARS))
    tasks = read(ROLE / "tasks/main.yml")

    assert variables["openclaw_version"] == "2026.7.1-2"
    assert variables["openclaw_cli_version_output"] == (
        "OpenClaw 2026.7.1-2 (0790d9f)"
    )
    assert "openclaw_cli_version" not in variables
    assert variables["openclaw_package_url"] == (
        "https://registry.npmjs.org/openclaw/-/openclaw-2026.7.1-2.tgz"
    )
    assert variables["openclaw_package_sha512"] == (
        "c9c177c8f71b8cde9b50f79a531e8c87abf37b58505a80f7093ff059c983edaf"
        "316871c745468095aabe945c4c1dfd6cb0480e0d50308e5cd8aa9dadc24619ee"
    )
    assert variables["openclaw_node_version"] == "24.19.0"
    assert variables["openclaw_node_sha256"] == (
        "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647"
    )
    assert "openclaw@latest" not in tasks
    assert "openclaw update" not in tasks
    assert 'checksum: "sha512:{{ openclaw_package_sha512 }}"' in tasks
    assert 'checksum: "sha256:{{ openclaw_node_sha256 }}"' in tasks
    assert "lib/node_modules/openclaw/package.json').version" in tasks
    assert "Publish the pinned OpenClaw CLI wrapper" in tasks
    assert (
        'exec {{ openclaw_node_current_root }}/bin/node '
        '{{ openclaw_entrypoint }} "$@"'
    ) in tasks
    assert "Verify the published OpenClaw CLI through a standard system path" in tasks
    assert "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in tasks


def test_native_openclaw_cli_version_output_is_exact_in_every_validation_path():
    role_tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    role_contract = next(
        task
        for task in role_tasks
        if task["name"] == "Require the pinned native OpenClaw deployment contract"
    )
    installed_check = next(
        task
        for task in role_tasks
        if task["name"] == "Verify the installed OpenClaw CLI version output"
    )
    published_check = next(
        task
        for task in role_tasks
        if task["name"]
        == "Verify the published OpenClaw CLI through a standard system path"
    )

    assert (
        "openclaw_cli_version_output == 'OpenClaw ' + openclaw_version + "
        "' (0790d9f)'"
        in role_contract["ansible.builtin.assert"]["that"]
    )
    assert installed_check["failed_when"] == (
        "openclaw_installed_cli_version.stdout | trim != "
        "openclaw_cli_version_output"
    )
    assert published_check["failed_when"] == (
        "openclaw_published_cli_version.stdout | trim != "
        "openclaw_cli_version_output"
    )

    expected_shell_assertion = (
        "/usr/local/bin/openclaw --version)\" = "
        "'{{ openclaw_cli_version_output }}'"
    )
    assert expected_shell_assertion in read(VALIDATE)
    assert expected_shell_assertion in read(STAGE_VALIDATE)
    assert "openclaw_cli_version }}" not in read(VALIDATE)
    assert "openclaw_cli_version }}" not in read(STAGE_VALIDATE)


def test_native_runtime_install_recovers_only_exact_incomplete_version_prefixes():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    raw_tasks = read(ROLE / "tasks/main.yml")

    node_probe = next(
        task for task in tasks if task["name"] == "Inspect the complete pinned Node.js runtime"
    )
    node_remove = next(
        task for task in tasks if task["name"] == "Remove only an incomplete pinned Node.js prefix"
    )
    release_probe = next(
        task for task in tasks if task["name"] == "Inspect the complete pinned OpenClaw release"
    )
    release_remove = next(
        task
        for task in tasks
        if task["name"] == "Remove only an incomplete pinned OpenClaw release prefix"
    )

    assert "bin/node" in node_probe["ansible.builtin.shell"]
    assert "bin/npm" in node_probe["ansible.builtin.shell"]
    assert "lib/node_modules/npm/bin/npm-cli.js" in node_probe["ansible.builtin.shell"]
    assert node_probe["failed_when"] is False
    assert node_remove["ansible.builtin.file"] == {
        "path": "{{ openclaw_node_release_root }}",
        "state": "absent",
    }
    assert node_remove["when"] == "openclaw_node_install_probe.rc != 0"
    node_extract = next(
        task for task in tasks if task["name"] == "Extract the pinned official Node.js runtime"
    )
    assert node_extract["notify"] == "Restart OpenClaw Gateway"
    assert "bin/openclaw" in release_probe["ansible.builtin.shell"]
    assert "openclaw.mjs" in release_probe["ansible.builtin.shell"]
    assert release_remove["ansible.builtin.file"] == {
        "path": "{{ openclaw_release_root }}",
        "state": "absent",
    }
    assert release_remove["when"] == "openclaw_release_install_probe.rc != 0"
    release_install = next(
        task
        for task in tasks
        if task["name"] == "Install the pinned OpenClaw release into its immutable prefix"
    )
    assert release_install["notify"] == "Restart OpenClaw Gateway"
    assert raw_tasks.count(".homelab-install-complete") == 4
    assert ".homelab-install-complete" in node_probe["ansible.builtin.shell"]
    assert ".homelab-install-complete" in release_probe["ansible.builtin.shell"]
    assert "creates: \"{{ openclaw_node_release_root }}/bin/node\"" not in raw_tasks
    assert "creates: \"{{ openclaw_release_root }}/lib/node_modules/openclaw/package.json\"" not in raw_tasks


def test_native_openclaw_is_staged_then_explicitly_activated():
    variables = yaml.safe_load(read(VARS))
    tasks_text = read(ROLE / "tasks/main.yml")
    tasks = yaml.safe_load(tasks_text)

    assert variables["openclaw_native_activate"] is False
    activation = next(
        task
        for task in tasks
        if task["name"] == "Activate only the native OpenClaw system service"
    )
    assert activation["when"] == [
        "openclaw_native_activate | bool",
        "not (openclaw_docker_rollback_activate | bool)",
    ]
    assert activation["ansible.builtin.systemd_service"] == {
        "name": "openclaw-gateway.service",
        "enabled": True,
        "state": "started",
        "daemon_reload": True,
    }
    staged = next(
        task
        for task in tasks
        if task["name"] == "Keep an uncut staged native OpenClaw service stopped"
    )
    assert staged["when"] == [
        "not (openclaw_native_activate | bool)",
        "not (openclaw_native_transition_marker.stat.exists | default(false))",
    ]
    assert staged["ansible.builtin.systemd_service"] == {
        "name": "openclaw-gateway.service",
        "enabled": False,
        "state": "stopped",
        "daemon_reload": True,
    }
    staged_when = [
        "not (openclaw_native_activate | bool)",
        "not (openclaw_native_transition_marker.stat.exists | default(false))",
    ]
    diagnostic = next(
        task
        for task in tasks
        if task["name"] == "Read allowlisted retained staged OpenClaw failure properties"
    )
    assert diagnostic["when"] == staged_when
    argv = diagnostic["ansible.builtin.command"]["argv"]
    assert argv[:3] == ["systemctl", "show", "openclaw-gateway.service"]
    assert set(argv[3:]) == {
        "--property=ActiveState",
        "--property=SubState",
        "--property=Result",
        "--property=ExecMainCode",
        "--property=ExecMainStatus",
        "--property=NRestarts",
    }
    report = next(
        task
        for task in tasks
        if task["name"] == "Report retained staged OpenClaw failure properties"
    )
    assert report["when"] == staged_when + [
        "'ActiveState=failed' in openclaw_staged_systemd_state.stdout_lines"
    ]
    assert report["ansible.builtin.debug"]["msg"] == (
        "{{ openclaw_staged_systemd_state.stdout_lines }}"
    )
    journal = next(
        task
        for task in tasks
        if task["name"]
        == "Classify retained staged OpenClaw application failure journal"
    )
    exact_failed_application_when = staged_when
    assert journal["ansible.builtin.include_tasks"] == (
        "classify_gateway_journal.yml"
    )
    assert journal["when"] == exact_failed_application_when
    require_cause = journal["vars"]["openclaw_journal_require_cause"]
    assert "'ActiveState=failed'" in require_cause
    assert "'ExecMainCode=1'" in require_cause
    assert "'ExecMainStatus=1'" in require_cause
    query_guard = next(
        task
        for task in tasks
        if task["name"]
        == "Preserve retained state when the staged OpenClaw journal query fails"
    )
    assert query_guard["when"] == exact_failed_application_when
    assert query_guard["ansible.builtin.assert"]["that"] == [
        "'journal-query-failed=0' in openclaw_journal_classification.stdout_lines"
    ]
    assert query_guard["ansible.builtin.assert"]["fail_msg"] == (
        "The bounded native OpenClaw journal query failed; no retained failure "
        "state was reset."
    )
    reset = next(
        task
        for task in tasks
        if task["name"] == "Reset the retained staged OpenClaw failure state"
    )
    assert reset["when"] == staged_when + [
        "'ActiveState=failed' in openclaw_staged_systemd_state.stdout_lines"
    ]
    assert reset["changed_when"] is False
    assert reset["ansible.builtin.command"]["argv"] == [
        "systemctl",
        "reset-failed",
        "openclaw-gateway.service",
    ]
    proof = next(
        task
        for task in tasks
        if task["name"] == "Prove the uncut staged OpenClaw service remains fenced"
    )
    assert proof["when"] == staged_when
    proof_shell = proof["ansible.builtin.shell"]
    assert "systemctl is-enabled openclaw-gateway.service" in proof_shell
    assert "systemctl is-active openclaw-gateway.service" in proof_shell
    assert "sport = :{{ openclaw_gateway_port }}" in proof_shell
    assert 'listeners="$(ss -H -ltn' in proof_shell
    assert 'test -z "${listeners}"' in proof_shell
    assert "! ss " not in proof_shell
    stage_task_names = [task["name"] for task in tasks]
    assert stage_task_names.index(staged["name"]) < stage_task_names.index(
        diagnostic["name"]
    ) < stage_task_names.index(report["name"]) < stage_task_names.index(
        journal["name"]
    ) < stage_task_names.index(query_guard["name"]) < stage_task_names.index(
        reset["name"]
    ) < stage_task_names.index(proof["name"])
    preserve = next(
        task
        for task in tasks
        if task["name"] == "Preserve the validated transitional native OpenClaw service"
    )
    assert preserve["when"] == [
        "not (openclaw_native_activate | bool)",
        "not (openclaw_docker_rollback_activate | bool)",
        "openclaw_native_transition_marker.stat.exists | default(false)",
    ]
    assert preserve["ansible.builtin.systemd_service"] == {
        "name": "openclaw-gateway.service",
        "enabled": True,
        "state": "started",
        "daemon_reload": True,
    }
    assert tasks_text.index("Flush validated OpenClaw handlers before activation") < (
        tasks_text.index("Preserve the validated transitional native OpenClaw service")
    )
    readiness = next(
        task
        for task in tasks
        if task["name"] == "Wait for the native OpenClaw Gateway readiness endpoint"
    )
    assert "openclaw_native_activate | bool or" in readiness["when"]
    assert "openclaw_native_transition_marker.stat.exists | default(false)" in (
        readiness["when"]
    )
    assert variables["openclaw_native_transition_marker_path"] == (
        "/var/lib/.openclaw-native-migration-validated"
    )
    assert variables["openclaw_native_transition_marker_value"] == (
        "homelab-openclaw-native-migration-v1"
    )


def test_native_openclaw_uses_an_external_hardened_system_service():
    unit = read(ROLE / "templates/openclaw-gateway.service.j2")

    for value in (
        "Requires=nftables.service",
        "After=network-online.target nftables.service",
        "User={{ openclaw_user }}",
        "Group={{ openclaw_group }}",
        "OPENCLAW_CONFIG_PATH={{ openclaw_config_path }}",
        "OPENCLAW_STATE_DIR={{ openclaw_state_root }}",
        "OPENCLAW_NO_AUTO_UPDATE=1",
        "OPENCLAW_NO_RESPAWN=1",
        "OPENCLAW_SERVICE_REPAIR_POLICY=external",
        "OPENCLAW_SUPERVISOR_MODE=external",
        "NODE_OPTIONS=--max-old-space-size=2048",
        "RestartPreventExitStatus=78",
        "OOMPolicy=continue",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectClock=true",
        "ProtectHostname=true",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "CapabilityBoundingSet=",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "SystemCallArchitectures=native",
        "ReadOnlyPaths={{ openclaw_config_path }}",
        "LoadCredential=openclaw_gateway_token:{{ openclaw_gateway_token_path }}",
        "Environment=OPENCLAW_GATEWAY_TOKEN_FILE=%d/openclaw_gateway_token",
        "ReadWritePaths={{ openclaw_runtime_root }}",
        "ReadWritePaths={{ openclaw_auth_profile_secret_root }}",
    ):
        assert value in unit
    assert "systemctl --user" not in unit
    assert "gateway install" not in unit


def test_native_openclaw_config_and_secrets_remain_separated():
    variables = yaml.safe_load(read(VARS))
    tasks = read(ROLE / "tasks/main.yml")

    assert variables["openclaw_setup_root"] == "/home/openclaw/openclaw-setup"
    assert variables["openclaw_config_path"] == (
        "{{ openclaw_setup_root }}/config/openclaw.json"
    )
    assert variables["openclaw_runtime_root"] == "/var/lib/openclaw"
    assert variables["openclaw_state_root"] == "/var/lib/openclaw"
    assert variables["openclaw_gateway_token_path"] == (
        "{{ openclaw_secret_root }}/gateway_token"
    )
    assert variables["openclaw_auth_profile_secret_root"] == (
        "{{ openclaw_home }}/.config/openclaw"
    )
    assert "stat.mode == '0640'" in tasks
    assert "not (openclaw_setup_paths.results[2].stat.islnk" in tasks
    assert "git_safe --no-pager ls-files --error-unmatch config/openclaw.json" in tasks
    assert "Reject stale container paths in the native OpenClaw config" in tasks
    assert "secrets\n          - audit\n          - --check\n          - --json" in tasks
    assert "plaintextCount != 0" in tasks
    assert "unresolvedRefCount != 0" in tasks
    assert 'owner: root\n    group: root\n    mode: "0600"' in tasks
    assert "OPENCLAW_SUPERVISOR_MODE: external" in tasks


def test_native_config_path_preflight_normalizes_only_the_cli_home_display_prefix():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    preflight = next(
        task
        for task in walk_tasks(tasks)
        if task["name"] == "Check the active native OpenClaw config path"
    )

    assert preflight["environment"]["OPENCLAW_HOME"] == "{{ openclaw_home }}"
    assert preflight["environment"]["OPENCLAW_CONFIG_PATH"] == (
        "{{ openclaw_config_path }}"
    )
    assert preflight["failed_when"] == (
        "openclaw_config_file_preflight.rc != 0 or "
        "(openclaw_config_file_preflight.stdout | trim | "
        "regex_replace('^[$]OPENCLAW_HOME/', openclaw_home + '/')) != "
        "openclaw_config_path"
    )

    home = "/home/openclaw"
    canonical = f"{home}/openclaw-setup/config/openclaw.json"

    def normalize(cli_output):
        prefix = "$OPENCLAW_HOME/"
        stripped = cli_output.strip()
        if stripped.startswith(prefix):
            return f"{home}/{stripped[len(prefix):]}"
        return stripped

    assert (
        normalize("$OPENCLAW_HOME/openclaw-setup/config/openclaw.json\n")
        == canonical
    )
    assert normalize(f"{canonical}\n") == canonical
    assert normalize("$HOME/openclaw-setup/config/openclaw.json\n") != canonical
    assert normalize("$OPENCLAW_HOME/other/openclaw.json\n") != canonical
    assert normalize("openclaw-setup/config/openclaw.json\n") != canonical


def test_native_config_preflight_proves_secretrefs_before_accepting_cli_redaction():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    all_tasks = list(walk_tasks(tasks))
    protected = next(
        task
        for task in all_tasks
        if task["name"] == "Require the protected native OpenClaw SecretRef schema"
    )
    cli = next(
        task
        for task in all_tasks
        if task["name"] == "Require the proxy-only native OpenClaw config values"
    )

    assert tasks.index(protected) < next(
        index
        for index, task in enumerate(tasks)
        if task["name"] == "Validate native config through an isolated service-user credential"
    )
    assert protected["when"] == "openclaw_native_activate | bool"
    assert protected["no_log"] is True
    assertions = " ".join(protected["ansible.builtin.assert"]["that"])
    assert ".secrets == {'providers': {'gateway_token_file':" in assertions
    assert "'source': 'file'" in assertions
    assert "'path': '${OPENCLAW_GATEWAY_TOKEN_FILE}'" in assertions
    assert "'mode': 'singleValue'" in assertions
    assert ".gateway.auth.token ==" in assertions
    assert "'provider': 'gateway_token_file'" in assertions
    assert "'id': 'value'" in assertions

    assert cli["failed_when"] == (
        "openclaw_native_config_values.rc != 0 or "
        "(openclaw_native_config_values.stdout | from_json) != "
        "(item.cli_value | default(item.value))"
    )
    redacted = {
        item["path"]: item
        for item in cli["loop"]
        if "cli_value" in item
    }
    assert set(redacted) == {
        "secrets.providers.gateway_token_file.source",
        "secrets.providers.gateway_token_file.path",
        "secrets.providers.gateway_token_file.mode",
    }
    assert {item["cli_value"] for item in redacted.values()} == {
        "__OPENCLAW_REDACTED__"
    }
    assert {item["value"] for item in redacted.values()} == {
        "file",
        "${OPENCLAW_GATEWAY_TOKEN_FILE}",
        "singleValue",
    }
    assert all(
        "cli_value" not in item
        for item in cli["loop"]
        if not item["path"].startswith("secrets.providers.gateway_token_file.")
    )


def test_active_native_validation_enforces_private_repository_boundaries():
    plays = yaml.safe_load(read(VALIDATE))
    native_play = next(play for play in plays if play.get("hosts") == "svc_openclaw")
    task = next(
        task
        for task in native_play["tasks"]
        if task["name"]
        == "Validate the active native OpenClaw private repository boundary"
    )
    shell = task["ansible.builtin.shell"]

    assert task["when"] == "openclaw_native_activate | bool"
    assert task["changed_when"] is False
    assert task["no_log"] is True
    assert task["args"]["chdir"] == "{{ openclaw_setup_root }}"
    assert task["environment"] == {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for required in (
        "git_safe branch --show-current",
        "git_safe rev-parse --verify HEAD",
        "git_safe status --porcelain=v1 --no-ahead-behind",
        "git_safe diff --quiet --no-ext-diff --no-textconv",
        "git_safe diff --cached --quiet --no-ext-diff --no-textconv",
        "git_safe --no-pager ls-files --error-unmatch config/openclaw.json",
        "(^|/)(state|runtime|auth|credentials|sessions|logs|cache|tmp|temp)(/|$)",
        "grep --quiet -F -f '{{ openclaw_gateway_token_path }}' --",
        'test "$tracked_token_status" -eq 1',
        'setup="$(realpath \'{{ openclaw_setup_root }}\')"',
        'runtime="$(realpath \'{{ openclaw_runtime_root }}\')"',
        'case "$runtime" in "$setup"|"$setup"/*)',
        'case "$setup" in "$runtime"|"$runtime"/*)',
    ):
        assert required in shell
    assert '-- "$gateway_token"' not in shell


def test_native_openclaw_ingress_is_only_from_traefik_with_token_auth():
    variables = yaml.safe_load(read(VARS))
    firewall = read(ROLE / "templates/nftables.conf.j2")
    tasks = read(ROLE / "tasks/main.yml")

    assert variables["openclaw_control_ui_origin"] == (
        "https://{{ openclaw_hostname }}"
    )
    assert variables["openclaw_proxy_ip"] == "{{ docker_apps_ip }}"
    assert "policy drop" in firewall
    assert (
        "ip saddr {{ openclaw_proxy_ip }} tcp dport "
        "{{ openclaw_gateway_port }} accept"
    ) in firewall
    assert "gateway.bind, value: custom" in tasks
    assert "gateway.customBindHost" in tasks
    assert "gateway.controlUi.enabled, value: true" in tasks
    assert "gateway.terminal.enabled, value: false" in tasks
    assert "gateway.auth.allowTailscale, value: false" in tasks
    assert 'gateway.tailscale.resetOnExit, value: false' in tasks
    assert "gateway.auth.mode, value: token" in tasks
    assert "gateway.controlUi.allowedOrigins" in tasks
    assert "gateway.trustedProxies" in tasks
    assert "gateway.allowRealIpFallback, value: false" in tasks
    assert "gateway.auth.rateLimit" in tasks
    assert "secrets.providers.gateway_token_file.path" in tasks
    assert 'value: "${OPENCLAW_GATEWAY_TOKEN_FILE}"' in tasks
    assert "trusted-proxy" not in tasks
    assert "gateway.publicOrigin" not in tasks
    assert "agents.defaults.workspace" not in tasks


def test_native_openclaw_activation_validates_before_starting():
    tasks = read(ROLE / "tasks/main.yml")
    parsed_tasks = yaml.safe_load(tasks)

    order = [
        "Require the protected private OpenClaw repository and regular config",
        "Require a clean tracked native OpenClaw config on main",
        "Validate the native OpenClaw config schema",
        "Require the proxy-only native OpenClaw config values",
        "Audit native OpenClaw secrets before startup",
        "Flush validated OpenClaw handlers before activation",
        "Activate only the native OpenClaw system service",
        "Wait for the native OpenClaw Gateway readiness endpoint",
        "Probe the active native OpenClaw Gateway RPC endpoint",
    ]
    positions = [tasks.index(name) for name in order]
    assert positions == sorted(positions)
    assert "Flush staged OpenClaw handlers before activation" not in tasks

    handlers = read(ROLE / "handlers/main.yml")
    assert "systemctl\n      - is-active\n      - --quiet" in handlers
    assert "openclaw_gateway_active_for_restart.rc == 0" in handlers
    assert "not (openclaw_docker_rollback_activate | bool)" in handlers
    assert handlers.index("Reload OpenClaw nftables policy") < handlers.index(
        "Inspect active OpenClaw Gateway before restart"
    )
    assert "ws://{{ openclaw_bind_host }}:{{ openclaw_gateway_port }}" in tasks
    assert "http://{{ openclaw_bind_host }}:{{ openclaw_gateway_port }}/readyz" in tasks
    for hardened_git_contract in (
        "/run/openclaw-native-empty-hooks",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_SYSTEM: /dev/null",
        "GIT_CONFIG_GLOBAL: /dev/null",
        "core.hooksPath",
        "core.fsmonitor=false",
        "core.attributesFile=/dev/null",
        "--no-ext-diff --no-textconv",
        "test ! -x .git/config",
    ):
        assert hardened_git_contract in tasks
    all_tasks = list(walk_tasks(parsed_tasks))
    audit = next(
        task
        for task in all_tasks
        if task["name"] == "Audit native OpenClaw secrets before startup"
    )
    assert audit["become"] is True
    assert audit["become_user"] == "{{ openclaw_user }}"

    credential_copy = next(
        task
        for task in all_tasks
        if task["name"] == "Materialize a read-only service-user Gateway credential"
    )
    assert credential_copy["ansible.builtin.copy"]["remote_src"] is True
    assert credential_copy["ansible.builtin.copy"]["owner"] == "{{ openclaw_user }}"
    assert credential_copy["ansible.builtin.copy"]["mode"] == "0400"
    assert credential_copy["no_log"] is True
    assert credential_copy["diff"] is False
    assert any(
        task["name"] == "Remove the temporary native OpenClaw credential directory"
        and task["ansible.builtin.file"]["state"] == "absent"
        for task in all_tasks
    )

    rpc_probe = next(
        task
        for task in all_tasks
        if task["name"] == "Probe the active native OpenClaw Gateway RPC endpoint"
    )
    rpc_argv = rpc_probe["ansible.builtin.command"]["argv"]
    assert "--token" not in rpc_argv
    assert rpc_argv[-3:] == [
        "--url",
        "ws://{{ openclaw_bind_host }}:{{ openclaw_gateway_port }}",
        "--json",
    ]
    assert rpc_probe["become_user"] == "{{ openclaw_user }}"
    assert "primaryTargetId != 'explicit'" in rpc_probe["failed_when"]
    assert "targets[0].connect.rpcOk" in rpc_probe["failed_when"]
    assert any(
        task["name"] == "Remove the temporary native RPC credential directory"
        and task["ansible.builtin.file"]["state"] == "absent"
        for task in all_tasks
    )


def test_native_readiness_failure_reports_only_allowlisted_systemd_properties():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    readiness = next(
        task
        for task in tasks
        if task["name"] == "Wait for the native OpenClaw Gateway readiness endpoint"
    )

    assert [task["name"] for task in readiness["rescue"]] == [
        "Read allowlisted native OpenClaw systemd failure properties",
        "Report allowlisted native OpenClaw systemd failure properties",
        "Classify native OpenClaw application failure journal safely",
        "Fail after the native OpenClaw readiness diagnostic",
    ]
    diagnostic = readiness["rescue"][0]
    argv = diagnostic["ansible.builtin.command"]["argv"]
    assert argv[:3] == ["systemctl", "show", "openclaw-gateway.service"]
    assert set(argv[3:]) == {
        "--property=ActiveState",
        "--property=SubState",
        "--property=Result",
        "--property=ExecMainCode",
        "--property=ExecMainStatus",
        "--property=NRestarts",
    }
    journal = readiness["rescue"][2]
    assert journal["ansible.builtin.include_tasks"] == (
        "classify_gateway_journal.yml"
    )
    rendered = yaml.safe_dump(readiness)
    for forbidden in (
        "journalctl",
        "systemctl status",
        "Environment",
        "ExecStart",
        "OPENCLAW_GATEWAY_TOKEN",
        "cat ",
    ):
        assert forbidden not in rendered
    assert readiness["rescue"][-1]["ansible.builtin.fail"]["msg"]


def test_native_journal_classifier_never_reports_raw_journal_material():
    tasks = yaml.safe_load(read(ROLE / "tasks/classify_gateway_journal.yml"))
    scanner, validator, require_valid, report = tasks

    assert scanner["ansible.builtin.script"] == {
        "cmd": "classify_openclaw_journal.py",
        "executable": "/usr/bin/python3",
    }
    assert scanner["register"] == "openclaw_journal_classification"
    assert scanner["changed_when"] is False
    assert scanner["failed_when"] is False
    assert scanner["no_log"] is True

    assert validator["register"] == (
        "openclaw_journal_classification_validation"
    )
    assert validator["changed_when"] is False
    assert validator["failed_when"] is False
    assert validator["no_log"] is True
    command = validator["ansible.builtin.command"]
    assert command["stdin"] == "{{ openclaw_journal_classification.stdout }}"
    assert command["stdin_add_newline"] is False
    validator_source = command["argv"][2]
    for contract in (
        "if text.endswith('\\n')",
        "raw.decode('ascii')",
        "len(lines) != len(keys)",
        "[0-9]{1,3}",
        "value > 200",
        "values['journal-query-failed'] > 1",
        "values['journal-truncated'] > 1",
        "sys.argv[2] not in ('0', '1')",
        "sys.argv[2] == '1'",
        "values['journal-records'] == 0",
        "sum(values[key] for key in keys[3:]) == 0",
    ):
        assert contract in validator_source

    assert require_valid["ansible.builtin.assert"]["that"] == [
        "openclaw_journal_classification_validation.rc == 0"
    ]
    assert report["ansible.builtin.debug"] == {
        "msg": "{{ openclaw_journal_classification.stdout_lines }}"
    }
    included = read(ROLE / "tasks/classify_gateway_journal.yml")
    assert "MESSAGE" not in included
    assert "journalctl" not in included
    assert "openclaw_journal_classification.stdout_lines" in included


def test_common_debian_leaves_uid_1000_for_the_dedicated_openclaw_account():
    debian_variables = yaml.safe_load(
        read(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/debian.yml")
    )
    openclaw_variables = yaml.safe_load(read(VARS))
    common_tasks = read(REPO_ROOT / "infra/ansible/roles/common_debian/tasks/main.yml")

    assert debian_variables["common_debian_create_service_account"] is True
    assert openclaw_variables["common_debian_create_service_account"] is False
    assert common_tasks.count(
        "when: common_debian_create_service_account | default(true) | bool"
    ) == 2
    assert openclaw_variables["openclaw_uid"] == 1000
    assert openclaw_variables["openclaw_gid"] == 1000


def test_site_and_validation_include_the_dedicated_openclaw_lxc():
    site = read(REPO_ROOT / "infra/ansible/playbooks/site.yml")
    validation = read(REPO_ROOT / "infra/ansible/playbooks/validate.yml")

    assert "hosts: svc_openclaw" in site
    assert "role: openclaw_native" in site
    assert site.index("Wait for tailnet route recovery") < site.index(
        "Stage or activate the dedicated native OpenClaw Gateway"
    )
    assert site.index("Stage or activate the dedicated native OpenClaw Gateway") < site.index(
        "Configure Docker Compose application LXC"
    )
    assert "Validate the dedicated native OpenClaw host" in validation
    assert "systemctl is-enabled --quiet openclaw-gateway.service" in validation
    assert "systemctl is-active --quiet openclaw-gateway.service" in validation
    assert "test ! -e /var/run/docker.sock" in validation
    assert "! command -v docker" in validation
    assert "systemctl is-active --quiet nftables" in validation
    assert "openclaw_native_activate | bool" in validation
    assert "Reject an ambiguous native transition marker" in validation
    assert "openclaw_native_transition_marker_value + '\\n'" in validation


def test_active_native_validation_rechecks_git_secret_and_path_boundaries():
    validation_text = read(REPO_ROOT / "infra/ansible/playbooks/validate.yml")
    validation = yaml.safe_load(validation_text)
    native_play = next(
        play
        for play in validation
        if play["name"] == "Validate the dedicated native OpenClaw host"
    )
    task = next(
        task
        for task in native_play["tasks"]
        if task["name"] == "Validate active native OpenClaw Git and secret boundaries"
    )
    script = task["ansible.builtin.shell"]

    assert task["when"] == "openclaw_native_activate | bool"
    assert task["changed_when"] is False
    assert task["no_log"] is True
    assert task["args"] == {
        "executable": "/bin/sh",
        "chdir": "{{ openclaw_setup_root }}",
    }
    assert task["environment"] == {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for contract in (
        "/run/openclaw-native-empty-hooks",
        "core.hooksPath=\"$hooks\"",
        "core.fsmonitor=false",
        "core.attributesFile=/dev/null",
        "test \"$(git_safe branch --show-current)\" = main",
        "git_safe rev-parse --verify HEAD",
        "git_safe status --porcelain=v1 --no-ahead-behind",
        "git_safe diff --quiet --no-ext-diff --no-textconv",
        "git_safe diff --cached --quiet --no-ext-diff --no-textconv",
        "ls-files --error-unmatch config/openclaw.json",
        "auth-profile-secrets|secrets|credentials|sessions|logs|cache|tmp|temp",
        "token_pattern='{{ openclaw_gateway_token_path }}'",
        "grep --quiet -F -f \"$token_pattern\" --",
        "test \"$tracked_token_status\" -eq 1",
        "realpath -e '{{ openclaw_setup_root }}'",
        "realpath -e '{{ openclaw_runtime_root }}'",
        "realpath -e '{{ openclaw_auth_profile_secret_root }}'",
        'case "$runtime" in "$setup"|"$setup"/*)',
        'case "$setup" in "$runtime"|"$runtime"/*)',
        'case "$auth" in "$setup"|"$setup"/*)',
        'case "$setup" in "$auth"|"$auth"/*)',
    ):
        assert contract in script
    assert 'gateway_token="$(' not in script
    assert "git_safe --no-pager grep -F --" not in script


def test_docker_host_has_a_minimal_native_cutover_finalizer():
    finalizer_path = (
        REPO_ROOT
        / "infra/ansible/playbooks/finalize-openclaw-native-cutover.yml"
    )
    finalizer = yaml.safe_load(read(finalizer_path))

    assert finalizer == [
        {
            "name": "Finalize the native OpenClaw cutover on the Docker host",
            "hosts": "svc_docker_apps",
            "gather_facts": True,
            "any_errors_fatal": True,
            "roles": [
                {
                    "role": "openclaw_foundation",
                    "tags": ["openclaw", "openclaw_native_cutover"],
                },
                {
                    "role": "arcane_manager",
                    "vars": {"arcane_openclaw_cutover_only": True},
                    "tags": ["arcane", "openclaw_native_cutover"],
                },
            ],
        }
    ]
    ci = read(REPO_ROOT / ".github/workflows/ci.yml")
    assert "finalize-openclaw-native-cutover.yml --syntax-check" in ci


def test_docker_foundation_repository_guard_is_a_well_formed_assertion():
    tasks = yaml.safe_load(
        read(
            REPO_ROOT
            / "infra/ansible/roles/openclaw_foundation/tasks/main.yml"
        )
    )
    task = next(
        task
        for task in walk_tasks(tasks)
        if task["name"]
        == "Require the protected private OpenClaw repository and regular config file"
    )

    assertion = task["ansible.builtin.assert"]
    assert set(assertion) == {"that", "fail_msg"}
    assert len(assertion["that"]) == 13
    assert assertion["fail_msg"] == (
        "The private openclaw-setup repository or active config has unexpected "
        "type, ownership, or permissions; refusing to deploy it."
    )
    assert "fail_msg" not in task
