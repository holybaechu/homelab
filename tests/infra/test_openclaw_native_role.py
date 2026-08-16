import os
import re
import subprocess
import sys

import pytest
import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_native"
VARS = REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"
VALIDATE = REPO_ROOT / "infra/ansible/playbooks/validate.yml"
STAGE_VALIDATE = (
    REPO_ROOT / "infra/ansible/playbooks/validate-openclaw-native-stage.yml"
)
MATERIALIZER = ROLE / "files/materialize_openclaw_credential.py"
PROBE_UNIT = ROLE / "templates/openclaw-credential-probe.service.j2"


def read(path):
    return path.read_text(encoding="utf-8")


def walk_tasks(tasks):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            yield from walk_tasks(task.get(section, []))


def tracked_path_classifier():
    validation = yaml.safe_load(read(VALIDATE))
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
    match = re.search(
        r'^python3 - "\$tracked_paths" <<\'PY\'\n(?P<classifier>.*?)^PY$',
        task["ansible.builtin.shell"],
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match.group("classifier")


def run_tracked_path_classifier(payload, tmp_path):
    manifest = tmp_path / "tracked-paths"
    manifest.write_bytes(payload)
    return subprocess.run(
        [sys.executable, "-", str(manifest)],
        input=tracked_path_classifier(),
        text=True,
        capture_output=True,
        check=False,
    )


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


def test_native_openclaw_activation_is_an_explicit_tracked_owner():
    variables = yaml.safe_load(read(VARS))
    tasks_text = read(ROLE / "tasks/main.yml")
    tasks = yaml.safe_load(tasks_text)

    assert isinstance(variables["openclaw_native_activate"], bool)
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
        "LoadCredential=ctf_docker_client_key:{{ openclaw_ctf_docker_client_key_path }}",
        "LoadCredential=ctf_docker_known_hosts:{{ openclaw_ctf_docker_known_hosts_path }}",
        "LoadCredential=discord_bot_token:{{ openclaw_discord_bot_token_path }}",
        "Environment=DOCKER_HOST={{ openclaw_ctf_docker_host }}",
        "Environment=PATH={{ openclaw_ctf_docker_ssh_shim_dir }}:{{ openclaw_node_current_root }}/bin:{{ openclaw_current_root }}/bin:/usr/local/bin:/usr/bin:/bin",
        "Environment=OPENCLAW_DISCORD_BOT_TOKEN_FILE=/run/openclaw-gateway/discord_bot_token",
        "RuntimeDirectory=openclaw-gateway",
        "RuntimeDirectoryMode=0700",
        "RuntimeDirectoryPreserve=no",
        "LimitCORE=0",
        "Environment=OPENCLAW_GATEWAY_TOKEN_FILE=/run/openclaw-gateway/gateway_token",
        "ExecStartPre=/usr/local/libexec/materialize-openclaw-credential "
        "%d/openclaw_gateway_token /run/openclaw-gateway/gateway_token",
        "ExecStartPre=/usr/local/libexec/materialize-openclaw-credential "
        "%d/discord_bot_token /run/openclaw-gateway/discord_bot_token",
        "ReadWritePaths={{ openclaw_runtime_root }}",
        "ReadWritePaths={{ openclaw_auth_profile_secret_root }}",
        "ReadWritePaths={{ openclaw_ctf_workspace_root }}",
        "InaccessiblePaths=-/run/docker.sock",
        "InaccessiblePaths=-/var/run/docker.sock",
    ):
        assert value in unit
    for forbidden in (
        "openclaw-ctf-gateway.service",
        "openclaw-discord-relay.service",
        "DOCKER_SSH_COMMAND",
    ):
        assert forbidden not in unit
    assert "systemctl --user" not in unit
    assert "gateway install" not in unit


def test_native_openclaw_materializes_the_systemd_credential_without_privilege():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    unit = read(ROLE / "templates/openclaw-gateway.service.j2")
    probe = read(PROBE_UNIT)

    install = next(
        task
        for task in tasks
        if task["name"] == "Install the bounded OpenClaw credential materializer"
    )
    assert install["ansible.builtin.copy"] == {
        "src": "materialize_openclaw_credential.py",
        "dest": "/usr/local/libexec/materialize-openclaw-credential",
        "owner": "root",
        "group": "root",
        "mode": "0755",
    }
    assert install["notify"] == "Restart OpenClaw Gateway"
    directory_task = next(
        task
        for task in tasks
        if task["name"]
        == "Create native OpenClaw installation and runtime directories"
    )
    assert {
        "path": "/usr/local/libexec",
        "owner": "root",
        "group": "root",
        "mode": "0755",
    } in directory_task["loop"]

    for rendered in (unit, probe):
        assert "User={{ openclaw_user }}" in rendered
        assert "Group={{ openclaw_group }}" in rendered
        assert (
            "LoadCredential=openclaw_gateway_token:"
            "{{ openclaw_gateway_token_path }}"
        ) in rendered
        assert (
            "LoadCredential=discord_bot_token:"
            "{{ openclaw_discord_bot_token_path }}"
        ) in rendered
        assert "/usr/local/libexec/materialize-openclaw-credential " in rendered
        assert "%d/openclaw_gateway_token " in rendered
        assert "%d/discord_bot_token " in rendered
        assert "RuntimeDirectoryMode=0700" in rendered
        assert "RuntimeDirectoryPreserve=no" in rendered
        assert "LimitCORE=0" in rendered
        assert "ExecStartPre=+" not in rendered
        assert "ExecStart=+" not in rendered
        assert "OPENCLAW_GATEWAY_TOKEN=" not in rendered

    assert "RuntimeDirectory=openclaw-gateway" in unit
    assert (
        "Environment=OPENCLAW_GATEWAY_TOKEN_FILE="
        "/run/openclaw-gateway/gateway_token"
    ) in unit
    assert (
        "Environment=OPENCLAW_GATEWAY_TOKEN_FILE=%d/openclaw_gateway_token"
        not in unit
    )
    assert (
        "Environment=OPENCLAW_DISCORD_BOT_TOKEN_FILE="
        "/run/openclaw-gateway/discord_bot_token"
    ) in unit
    assert "Environment=OPENCLAW_DISCORD_BOT_TOKEN_FILE=%d/discord_bot_token" not in unit
    assert "RuntimeDirectory=openclaw-credential-probe" in probe
    assert (
        "ExecStartPre=/usr/local/libexec/materialize-openclaw-credential "
        "%d/openclaw_gateway_token /run/openclaw-credential-probe/gateway_token"
    ) in probe
    assert (
        "ExecStartPre=/usr/local/libexec/materialize-openclaw-credential "
        "%d/discord_bot_token /run/openclaw-credential-probe/discord_bot_token"
    ) in probe
    assert probe.count("ExecStart=") == 1
    assert "Environment=" not in probe
    assert "openclaw.mjs" not in probe
    assert "gateway --port" not in probe


def test_native_credential_probe_is_bounded_no_log_and_removes_all_residue():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    probe = next(
        task
        for task in tasks
        if task["name"]
        == "Validate the native systemd credential materialization contract"
    )

    assert probe["when"] == "not (openclaw_docker_rollback_activate | bool)"
    assert probe["no_log"] is True
    assert [task["name"] for task in probe["block"]] == [
        "Install the transient OpenClaw credential contract probe",
        "Reload systemd for the OpenClaw credential contract probe",
        "Run the native OpenClaw credential contract probe",
        "Prove the native OpenClaw credential contract probe completed",
    ]
    rendered = probe["block"][0]["ansible.builtin.template"]
    assert rendered == {
        "src": "openclaw-credential-probe.service.j2",
        "dest": "/run/systemd/system/openclaw-credential-probe.service",
        "owner": "root",
        "group": "root",
        "mode": "0600",
    }
    run = probe["block"][2]["ansible.builtin.systemd_service"]
    assert run == {
        "name": "openclaw-credential-probe.service",
        "state": "started",
    }
    proof = probe["block"][3]["ansible.builtin.shell"]
    assert "--property=Result --value" in proof
    assert "--property=ExecMainStatus --value" in proof
    assert "= inactive" in proof
    assert "test ! -e /run/openclaw-credential-probe" in proof
    assert "openclaw-gateway.service" not in proof
    assert [task["name"] for task in probe["always"]] == [
        "Stop the native OpenClaw credential contract probe",
        "Remove the transient OpenClaw credential contract probe unit",
        "Reload systemd after the OpenClaw credential contract probe",
        "Prove the native OpenClaw credential contract probe left no residue",
    ]
    cleanup = probe["always"][-1]["ansible.builtin.shell"]
    assert "test ! -e /run/systemd/system/openclaw-credential-probe.service" in cleanup
    assert "test ! -L /run/systemd/system/openclaw-credential-probe.service" in cleanup
    assert "test ! -e /run/openclaw-credential-probe" in cleanup
    assert "test ! -L /run/openclaw-credential-probe" in cleanup
    assert "LoadState --value)\" = not-found" in cleanup
    assert "FragmentPath --value)\"" in cleanup
    assert "ActiveState --value)\" = inactive" in cleanup
    assert "SubState --value)\" = dead" in cleanup
    assert "systemctl is-enabled openclaw-credential-probe.service" in cleanup
    assert "list-unit-files" not in cleanup
    assert "/etc/systemd/system/openclaw-credential-probe.service" in cleanup
    assert (
        "/etc/systemd/system/multi-user.target.wants/"
        "openclaw-credential-probe.service" in cleanup
    )
    assert "find /etc/systemd/system /run/systemd/system -xdev -type l" in cleanup
    assert "-lname '*openclaw-credential-probe.service'" in cleanup
    assert "-print -quit" in cleanup
    assert 'test -z "${links}"' in cleanup
    assert "openclaw-gateway.service" not in cleanup


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
    assert variables["openclaw_codex_profile_id"] == "openai:main"
    assert variables["openclaw_codex_model"] == "openai/gpt-5.6-terra"
    assert variables["openclaw_codex_thinking_default"] == "xhigh"
    assert variables["openclaw_codex_plugin_spec"] == (
        "npm:@openclaw/codex@2026.7.1-1"
    )
    assert variables["openclaw_discord_plugin_spec"] == (
        "npm:@openclaw/discord@2026.7.1"
    )
    assert "stat.mode == '0640'" in tasks
    assert "not (openclaw_setup_paths.results[2].stat.islnk" in tasks
    assert "git_safe --no-pager ls-files --error-unmatch config/openclaw.json" in tasks
    assert "Reject stale container paths in the native OpenClaw config" in tasks
    assert "secrets\n          - audit\n          - --check\n          - --json" in tasks
    assert "openclaw_secrets_audit_result.summary.plaintextCount == 0" in tasks
    assert "openclaw_secrets_audit_result.summary.unresolvedRefCount == 0" in tasks
    assert "openclaw_secrets_audit_result.summary.shadowedRefCount == 0" in tasks
    assert "openclaw_secrets_audit_result.summary.legacyResidueCount in [0, 1]" in tasks
    assert "findings[0].code == 'LEGACY_RESIDUE'" in tasks
    assert "findings[0].severity == 'info'" in tasks
    assert "findings[0].jsonPath == 'profiles.' + openclaw_codex_profile_id" in tasks
    assert "findings[0].provider == 'openai'" in tasks
    assert "findings[0].profileId == openclaw_codex_profile_id" in tasks
    assert "openclaw_secrets_audit_preflight.rc == 1" in tasks
    assert "codes={{ openclaw_secrets_audit_result.findings" in tasks
    assert "paths={{ openclaw_secrets_audit_result.findings" in tasks
    assert 'owner: root\n    group: root\n    mode: "0600"' in tasks
    assert "OPENCLAW_SUPERVISOR_MODE: external" in tasks
    assert "openclaw-discord-relay-core" not in tasks


def test_native_openclaw_uses_the_pinned_codex_subscription_harness_for_all_agents():
    variables = yaml.safe_load(read(VARS))
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    all_tasks = list(walk_tasks(tasks))

    contract = next(
        task
        for task in all_tasks
        if task["name"] == "Require the protected one-Gateway OpenClaw schema"
    )
    assertions = "\n".join(contract["ansible.builtin.assert"]["that"])
    for required in (
        "agents.defaults.model.primary == openclaw_codex_model",
        "agents.defaults.thinkingDefault == openclaw_codex_thinking_default",
        "'order': {'openai': [openclaw_codex_profile_id]}",
        "models.providers.openai ==",
        "plugins.entries.codex.enabled",
        "plugins.entries.discord.enabled",
        "openclaw_ctf_agents | length == 1",
        "openclaw_main_agents | length == 1",
        "sandbox.backend == 'docker'",
    ):
        assert required in assertions
    assert "apiKey" not in assertions

    npm_cache_directories = next(
        task
        for task in all_tasks
        if task["name"]
        == "Create native OpenClaw installation and runtime directories"
    )
    assert variables["openclaw_cache_root"] == "/var/cache/openclaw"
    assert variables["openclaw_npm_cache_root"] == (
        "{{ openclaw_cache_root }}/npm"
    )
    assert {
        "path": "{{ openclaw_npm_cache_root }}",
        "owner": "{{ openclaw_user }}",
        "group": "{{ openclaw_group }}",
        "mode": "0700",
    } in npm_cache_directories["loop"]
    assert "{{ openclaw_home }}/.npm" not in read(ROLE / "tasks/main.yml")

    install = next(
        task
        for task in all_tasks
        if task["name"] == "Install the pinned core Codex harness"
    )
    assert install["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "install",
        "{{ openclaw_codex_plugin_spec }}",
        "--pin",
        "--force",
    ]
    assert install["become_user"] == "{{ openclaw_user }}"
    assert install["environment"]["OPENCLAW_CONFIG_PATH"] == (
        "{{ openclaw_validation_credential_dir.path }}/plugin-install/plugin-install-config.json"
    )
    assert install["environment"]["OPENCLAW_STATE_DIR"] == "{{ openclaw_state_root }}"
    assert install["environment"]["NPM_CONFIG_CACHE"] == (
        "{{ openclaw_npm_cache_root }}"
    )

    plugin_install_dir = next(
        task
        for task in all_tasks
        if task["name"] == "Create a writable native OpenClaw plugin-install directory"
    )
    assert plugin_install_dir["ansible.builtin.file"] == {
        "path": "{{ openclaw_validation_credential_dir.path }}/plugin-install",
        "state": "directory",
        "owner": "{{ openclaw_user }}",
        "group": "{{ openclaw_group }}",
        "mode": "0700",
    }

    writable_config = next(
        task
        for task in all_tasks
        if task["name"] == "Materialize a writable core plugin-install config"
    )
    assert writable_config["ansible.builtin.copy"] == {
        "src": "{{ openclaw_config_path }}",
        "dest": "{{ openclaw_validation_credential_dir.path }}/plugin-install/plugin-install-config.json",
        "remote_src": True,
        "owner": "{{ openclaw_user }}",
        "group": "{{ openclaw_group }}",
        "mode": "0600",
    }

    runtime = next(
        task
        for task in all_tasks
        if task["name"] == "Inspect the core Codex harness runtime before startup"
    )
    assert runtime["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "inspect",
        "codex",
        "--runtime",
        "--json",
    ]
    assert ".plugin.id != 'codex'" in runtime["failed_when"]
    assert ".plugin.status != 'loaded'" in runtime["failed_when"]
    assert runtime["no_log"] is True
    assert "NPM_CONFIG_CACHE" not in runtime["environment"]

    assert "openclaw_ctf_codex_profile_id" not in variables


def test_native_openclaw_installs_and_loads_the_pinned_discord_channel_plugin():
    tasks = yaml.safe_load(read(ROLE / "tasks/main.yml"))
    all_tasks = list(walk_tasks(tasks))

    install = next(
        task
        for task in all_tasks
        if task["name"] == "Install the pinned core Discord channel plugin"
    )
    assert install["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "install",
        "{{ openclaw_discord_plugin_spec }}",
        "--pin",
        "--force",
    ]
    assert install["become_user"] == "{{ openclaw_user }}"
    assert install["environment"]["OPENCLAW_CONFIG_PATH"] == (
        "{{ openclaw_validation_credential_dir.path }}/plugin-install/plugin-install-config.json"
    )
    assert install["environment"]["NPM_CONFIG_CACHE"] == (
        "{{ openclaw_npm_cache_root }}"
    )

    runtime = next(
        task
        for task in all_tasks
        if task["name"] == "Inspect the core Discord channel plugin runtime before startup"
    )
    assert runtime["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "inspect",
        "discord",
        "--runtime",
        "--json",
    ]
    assert ".plugin.id != 'discord'" in runtime["failed_when"]
    assert ".plugin.status != 'loaded'" in runtime["failed_when"]
    assert runtime["become_user"] == "{{ openclaw_user }}"
    assert runtime["no_log"] is True


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
        if task["name"] == "Require the protected one-Gateway OpenClaw schema"
    )
    discord_boundary = next(
        task
        for task in all_tasks
        if task["name"]
        == "Require channel-only shared Discord routing in the one Gateway"
    )
    cli = next(
        task
        for task in all_tasks
        if task["name"] == "Require the proxy-only native OpenClaw config values"
    )
    config_preflight_index = next(
        index
        for index, task in enumerate(tasks)
        if task["name"] == "Validate native config through an isolated service-user credential"
    )
    assert tasks.index(protected) < tasks.index(discord_boundary) < config_preflight_index
    assert protected["when"] == "openclaw_native_activate | bool"
    assert protected["no_log"] is True
    assertions = " ".join(protected["ansible.builtin.assert"]["that"])
    assert ".secrets ==" in assertions
    assert "'gateway_token_file':" in assertions
    assert "'source': 'file'" in assertions
    assert "'path': '${OPENCLAW_GATEWAY_TOKEN_FILE}'" in assertions
    assert "'mode': 'singleValue'" in assertions
    assert ".gateway.auth.token ==" in assertions
    assert "'provider': 'gateway_token_file'" in assertions
    assert "'id': 'value'" in assertions
    assert "discord_bot_token_file" in assertions
    assert "plugins.entries.discord.enabled" in assertions
    assert "openclaw_ctf_agents | length == 1" in assertions
    assert "openclaw_main_agents | length == 1" in assertions
    assert "sandbox.backend == 'docker'" in assertions
    assert "sandbox.scope == 'session'" in assertions
    assert assertions.count("'message' in") == 3
    assert "'message' not in (openclaw_ctf_agents[0].tools.sandbox.tools.deny" in assertions
    assert "openclaw_ctf_agents[0].tools.message.crossContext" in assertions
    assert "openclaw_main_agents[0].tools.message.crossContext" in assertions

    assert discord_boundary["when"] == "openclaw_native_activate | bool"
    assert discord_boundary["no_log"] is True
    boundary_assertions = " ".join(
        discord_boundary["ansible.builtin.assert"]["that"]
    )
    assert "openclaw_discord_account.token" in boundary_assertions
    assert "openclaw_discord_allowed_channels" in boundary_assertions
    assert "selectattr('agentId', 'equalto', 'ctf')" in boundary_assertions
    assert "openclaw_discord_account" in discord_boundary["vars"]

    assert cli["failed_when"] == (
        "openclaw_native_config_values.rc != 0 or "
        "(openclaw_native_config_values.stdout | from_json) != "
        "(item.cli_value | default(item.value))"
    )
    redacted = {
        item["path"]: item
        for item in cli["loop"]
        if "cli_value" in item
        and item["path"].startswith("secrets.providers.")
    }
    assert set(redacted) == {
        "secrets.providers.gateway_token_file.source",
        "secrets.providers.gateway_token_file.path",
        "secrets.providers.gateway_token_file.mode",
        "secrets.providers.discord_bot_token_file.source",
        "secrets.providers.discord_bot_token_file.path",
        "secrets.providers.discord_bot_token_file.mode",
    }
    assert {item["cli_value"] for item in redacted.values()} == {
        "__OPENCLAW_REDACTED__"
    }
    assert {item["value"] for item in redacted.values()} == {
        "file",
        "${OPENCLAW_GATEWAY_TOKEN_FILE}",
        "singleValue",
        "${OPENCLAW_DISCORD_BOT_TOKEN_FILE}",
    }
    provider_runtime = next(
        item
        for item in cli["loop"]
        if item["path"] == "models.providers.openai.agentRuntime.id"
    )
    assert provider_runtime == {
        "path": "models.providers.openai.agentRuntime.id",
        "value": "codex",
    }
    assert all(
        "cli_value" not in item
        for item in cli["loop"]
        if not item["path"].startswith("secrets.providers.")
    )
    assert {
        item["path"]: item["value"]
        for item in cli["loop"]
        if item["path"].startswith("plugins.entries.")
    } == {
        "plugins.entries.codex.enabled": True,
        "plugins.entries.discord.enabled": True,
    }


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
        "Install the pinned core Codex harness",
        "Validate the native OpenClaw config schema",
        "Inspect the core Codex harness runtime before startup",
        "Require the proxy-only native OpenClaw config values",
        "Audit native OpenClaw secrets before startup",
        "Parse the native OpenClaw secret audit result",
        "Require a clean native OpenClaw secret audit",
        "Flush validated OpenClaw handlers before activation",
        "Activate only the native OpenClaw system service",
        "Wait for the native OpenClaw Gateway readiness endpoint",
        "Collect the active native Gateway scope-limited token handshake",
        "Prove the exact native Gateway scope-limited token handshake contract",
    ]
    positions = [tasks.index(name) for name in order]
    assert positions == sorted(positions)
    assert "Flush staged OpenClaw handlers before activation" not in tasks

    retired_ctf_mountpoint = next(
        task
        for task in walk_tasks(parsed_tasks)
        if task["name"] == "Retire the empty legacy CTF workspace mountpoint"
    )
    retirement = retired_ctf_mountpoint["ansible.builtin.shell"]
    assert "test -d \"$legacy\"" in retirement
    assert "test ! -L \"$legacy\"" in retirement
    assert "! mountpoint -q \"$legacy\"" in retirement
    assert "find \"$legacy\" -mindepth 1 -print -quit" in retirement
    assert "rmdir \"$legacy\"" in retirement
    assert "rm -r" not in retirement
    assert retired_ctf_mountpoint["when"] == "openclaw_native_activate | bool"

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
    secret_ref_contract = next(
        task
        for task in all_tasks
        if task["name"] == "Require the protected one-Gateway OpenClaw schema"
    )
    assert any("'remote' not in" in check for check in secret_ref_contract["ansible.builtin.assert"]["that"])
    assert secret_ref_contract["no_log"] is True
    audit = next(
        task
        for task in all_tasks
        if task["name"] == "Audit native OpenClaw secrets before startup"
    )
    assert audit["become"] is True
    assert audit["become_user"] == "{{ openclaw_user }}"
    assert audit["failed_when"] is False
    assert audit["no_log"] is True

    audit_assert = next(
        task
        for task in all_tasks
        if task["name"] == "Require a clean native OpenClaw secret audit"
    )
    assert audit_assert.get("no_log") is not True
    fail_msg = audit_assert["ansible.builtin.assert"]["fail_msg"]
    assert "codes=" in fail_msg
    assert "paths=" in fail_msg
    assert "message" not in fail_msg

    credential_dir = next(
        task
        for task in all_tasks
        if task["name"] == "Protect the temporary native OpenClaw credential directory"
    )
    assert credential_dir["ansible.builtin.file"] == {
        "path": "{{ openclaw_validation_credential_dir.path }}",
        "state": "directory",
        "owner": "root",
        "group": "{{ openclaw_group }}",
        "mode": "0750",
    }

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
    discord_credential_copy = next(
        task
        for task in all_tasks
        if task["name"]
        == "Materialize a read-only shared Discord validation credential"
    )
    assert discord_credential_copy["ansible.builtin.copy"]["src"] == (
        "{{ openclaw_discord_bot_token_path }}"
    )
    assert discord_credential_copy["ansible.builtin.copy"]["mode"] == "0400"
    assert discord_credential_copy["no_log"] is True
    config_path_preflight = next(
        task
        for task in all_tasks
        if task["name"] == "Check the active native OpenClaw config path"
    )
    assert config_path_preflight["environment"][
        "OPENCLAW_GATEWAY_TOKEN_FILE"
    ] == "{{ openclaw_validation_credential_dir.path }}/openclaw_gateway_token"
    assert config_path_preflight["environment"][
        "OPENCLAW_DISCORD_BOT_TOKEN_FILE"
    ] == "{{ openclaw_validation_credential_dir.path }}/discord_bot_token"
    assert "OPENCLAW_DISCORD_RELAY_CORE_HMAC_FILE" not in config_path_preflight["environment"]
    assert any(
        task["name"] == "Remove the temporary native OpenClaw credential directory"
        and task["ansible.builtin.file"]["state"] == "absent"
        for task in all_tasks
    )

    gateway_handshake = next(
        task
        for task in all_tasks
        if task["name"] == "Collect the active native Gateway scope-limited token handshake"
    )
    handshake_argv = gateway_handshake["ansible.builtin.command"]["argv"]
    assert "--token" not in handshake_argv
    assert handshake_argv[-3:] == [
        "--url",
        "ws://{{ openclaw_bind_host }}:{{ openclaw_gateway_port }}",
        "--json",
    ]
    assert gateway_handshake["become_user"] == "{{ openclaw_user }}"
    assert gateway_handshake["no_log"] is True
    assert gateway_handshake["environment"] == {
        "HOME": "{{ openclaw_home }}",
        "PATH": "{{ openclaw_node_current_root }}/bin:{{ openclaw_current_root }}/bin:/usr/local/bin:/usr/bin:/bin",
        "OPENCLAW_HOME": "{{ openclaw_home }}",
        "OPENCLAW_STATE_DIR": "{{ openclaw_validation_handshake_state_dir.path }}",
        "OPENCLAW_CONFIG_PATH": "{{ openclaw_config_path }}",
        "OPENCLAW_WORKSPACE_DIR": "{{ openclaw_validation_handshake_state_dir.path }}/workspace",
        "OPENCLAW_DISABLE_BONJOUR": "1",
        "OPENCLAW_NO_AUTO_UPDATE": "1",
        "OPENCLAW_NO_RESPAWN": "1",
        "OPENCLAW_SERVICE_REPAIR_POLICY": "external",
        "OPENCLAW_SUPERVISOR_MODE": "external",
        "OPENCLAW_AUTH_STORE_READONLY": "1",
        "OPENCLAW_GATEWAY_TOKEN_FILE": "{{ openclaw_gateway_handshake_credential_dir.path }}/openclaw_gateway_token",
        "OPENCLAW_DISCORD_BOT_TOKEN_FILE": "{{ openclaw_gateway_handshake_credential_dir.path }}/discord_bot_token",
    }
    handshake_source = tasks.split(
        "    - name: Collect the active native Gateway scope-limited token handshake\n", 1
    )[1].split("      register: openclaw_native_gateway_handshake\n", 1)[0]
    assert "<<:" not in handshake_source
    handshake_environment_source = handshake_source.split("      environment:\n", 1)[1]
    handshake_environment_keys = re.findall(
        r"^        ([A-Z_]+):", handshake_environment_source, flags=re.MULTILINE
    )
    assert len(handshake_environment_keys) == len(set(handshake_environment_keys))
    assert gateway_handshake["failed_when"] is False
    assert gateway_handshake["ansible.builtin.command"]["argv"][:4] == [
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=5s",
        "45s",
    ]
    handshake_assertion = next(
        task
        for task in all_tasks
        if task["name"]
        == "Prove the exact native Gateway scope-limited token handshake contract"
    )
    assert handshake_assertion["no_log"] is True
    handshake_contract = "\n".join(
        handshake_assertion["ansible.builtin.assert"]["that"]
    )
    for contract in (
        "openclaw_native_gateway_handshake.rc == 0",
        "openclaw_native_gateway_handshake_payload.ok",
        "openclaw_native_gateway_handshake_payload.degraded",
        "'connected_no_operator_scope'",
        "primaryTargetId == 'explicit'",
        "['explicit', 'localLoopback']",
        "explicit_targets | length == 1",
        ".kind == 'explicit'",
        ".active",
        ".connect.ok",
        ".connect.rpcOk",
        ".connect.scopeLimited",
        ".auth.role == 'operator'",
        ".auth.scopes == []",
        "loopback_targets | length == 1",
        "'ws://127.0.0.1:18789'",
        "warnings | length == 1",
        "'probe_scope_limited'",
        "['explicit']",
    ):
        assert contract in handshake_contract
    state_checks = [
        task
        for task in all_tasks
        if task["name"]
        in {
            "Prove the isolated native Gateway handshake state starts without device identity",
        }
    ]
    assert len(state_checks) == 1
    for state_check in state_checks:
        state_shell = state_check["ansible.builtin.shell"]
        assert "test -w" in state_shell
        assert "/identity" in state_shell
        assert "test ! -e" in state_shell
        assert "test ! -L" in state_shell
        assert state_check["become_user"] == "{{ openclaw_user }}"
    assert "-mindepth 1 -print -quit" in state_checks[0]["ansible.builtin.shell"]
    assert any(
        task["name"]
        == "Remove the temporary native Gateway handshake credential directory"
        and task["ansible.builtin.file"]["state"] == "absent"
        for task in all_tasks
    )
    handshake_credential_copy = next(
        task
        for task in all_tasks
        if task["name"]
        == "Materialize a read-only service-user Gateway handshake credential"
    )
    assert "openclaw_gateway_handshake_credential_dir.path" in (
        handshake_credential_copy["ansible.builtin.copy"]["dest"]
    )
    assert handshake_credential_copy["no_log"] is True
    handshake_discord_credential_copy = next(
        task
        for task in all_tasks
        if task["name"]
        == "Materialize a read-only shared Discord Gateway handshake credential"
    )
    assert handshake_discord_credential_copy["ansible.builtin.copy"]["src"] == (
        "{{ openclaw_discord_bot_token_path }}"
    )
    assert "openclaw_gateway_handshake_credential_dir.path" in (
        handshake_discord_credential_copy["ansible.builtin.copy"]["dest"]
    )
    assert handshake_discord_credential_copy["ansible.builtin.copy"]["mode"] == "0400"
    assert handshake_discord_credential_copy["no_log"] is True
    assert any(
        task["name"] == "Remove the isolated native Gateway handshake state directory"
        and task["ansible.builtin.file"]["state"] == "absent"
        for task in all_tasks
    )
    residue_assertion = next(
        task
        for task in all_tasks
        if task["name"] == "Require clean native Gateway handshake probe residue"
    )
    residue_contract = "\n".join(residue_assertion["ansible.builtin.assert"]["that"])
    for contract in (".stat.uid", ".stat.gid", ".stat.mode", "'0700'", "identity", "after_cleanup"):
        assert contract in residue_contract
    assert residue_assertion["no_log"] is True


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
    assert command["stdin"] == (
        "{{ openclaw_journal_classification.stdout_lines | to_json }}"
    )
    assert command["stdin_add_newline"] is False
    validator_source = command["argv"][2]
    for contract in (
        "json.loads(raw.decode('utf-8'))",
        "line.encode('ascii')",
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
    assert "role: openclaw_ctf_gateway" not in site
    assert "role: openclaw_discord_relay" not in site
    assert site.index("Wait for tailnet route recovery") < site.index(
        "Configure the isolated CTF Docker executor"
    )
    assert site.index(
        "Configure the isolated CTF Docker executor"
    ) < site.index("Stage or activate the dedicated native OpenClaw Gateway")
    assert site.index(
        "Stage or activate the dedicated native OpenClaw Gateway"
    ) < site.index("Connect the native OpenClaw Gateway to the isolated CTF executor")
    assert site.index(
        "Connect the native OpenClaw Gateway to the isolated CTF executor"
    ) < site.index("Configure Docker Compose application LXC")

    assert "Validate the dedicated native OpenClaw host" in validation
    assert "systemctl is-enabled --quiet openclaw-gateway.service" in validation
    assert "systemctl is-active --quiet openclaw-gateway.service" in validation
    assert "! systemctl is-active --quiet openclaw-ctf-gateway.service" in validation
    assert "! systemctl is-active --quiet openclaw-discord-relay.service" in validation
    assert "test ! -S /var/run/docker.sock" in validation
    assert "test -x '{{ openclaw_ctf_docker_cli_path }}'" in validation
    assert "! systemctl cat docker.service" in validation
    assert "Validate the Gateway service credential-scoped remote CTF Docker transport" in validation
    assert "openclaw_ctf_docker_cli_path" in validation
    assert "openclaw_ctf_user" not in validation
    assert "systemctl cat openclaw-gateway.service | grep -Fqx 'ReadWritePaths={{ openclaw_ctf_workspace_root }}'" in validation
    assert "InaccessiblePaths=-/var/run/docker.sock" in validation
    assert "Environment=OPENCLAW_DISCORD_BOT_TOKEN_FILE=/run/openclaw-gateway/discord_bot_token" in validation
    assert (
        "ExecStartPre=/usr/local/libexec/materialize-openclaw-credential "
        "%d/discord_bot_token /run/openclaw-gateway/discord_bot_token"
    ) in validation
    assert "systemctl is-active --quiet nftables" in validation
    assert "openclaw_native_activate | bool" in validation
    assert "Reject an ambiguous native transition marker" in validation
    assert "openclaw_native_transition_marker_value + '\\n'" in validation


def test_native_validation_rechecks_the_runtime_credential_boundary():
    for validation_path, task_name in (
        (STAGE_VALIDATE, "Check the staged or active native credential boundary"),
        (VALIDATE, "Check the native Gateway runtime credential boundary"),
    ):
        plays = yaml.safe_load(read(validation_path))
        native_play = next(
            play for play in plays if play.get("hosts") == "svc_openclaw"
        )
        task = next(
            task for task in native_play["tasks"] if task["name"] == task_name
        )
        shell = task["ansible.builtin.shell"]
        assert task["changed_when"] is False
        assert task["no_log"] is True
        assert "credential=/run/openclaw-gateway/gateway_token" in shell
        assert "discord_credential=/run/openclaw-gateway/discord_bot_token" in shell
        assert 'test -f "$credential"' in shell
        assert 'test ! -L "$credential"' in shell
        assert "stat -c '%u:%g %a %h %s'" in shell
        assert (
            '"{{ openclaw_uid }}:{{ openclaw_gid }} 400 1 65"' in shell
        )
        assert "grep -Eq '^[0-9a-fA-F]{64}$'" in shell
        assert 'test -f "$discord_credential"' in shell
        assert 'test ! -L "$discord_credential"' in shell
        assert 'discord_metadata="$(stat -c' in shell
        assert '"{{ openclaw_uid }}:{{ openclaw_gid }} 400 1 "*' in shell
        assert 'discord_size="${discord_metadata##* }"' in shell
        assert 'test "$discord_size" -ge 1' in shell
        assert 'test "$discord_size" -le 4097' in shell
        assert "test ! -e /run/openclaw-credential-probe" in shell
        assert (
            "test ! -e /run/systemd/system/"
            "openclaw-credential-probe.service" in shell
        )
        assert "cat " not in shell
        assert "printf" not in shell


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
        'tracked_paths="$(mktemp /run/openclaw-native-tracked-paths.XXXXXX)"',
        'trap cleanup_tracked_paths EXIT',
        'trap \'exit 1\' HUP INT TERM',
        'rm -f -- "$tracked_paths"',
        'stat -c \'%u:%g %a %h\' "$tracked_paths"',
        'git_safe --no-pager ls-files -z > "$tracked_paths"',
        'python3 - "$tracked_paths" <<\'PY\'',
        'allowed = b".env.example"',
        'b"auth-profile-secrets"',
        'component.startswith(b".env") or component in sensitive',
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
    assert "$(git_safe --no-pager ls-files -z" not in script


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b".env.example\0",
        b"README.md\0config/openclaw.json\0",
        b"nested/authentication/README\0",
    ),
)
def test_active_native_tracked_path_classifier_accepts_safe_nul_manifests(
    payload, tmp_path
):
    result = run_tracked_path_classifier(payload, tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "payload",
    (
        b".env\0",
        b".env.prod\0",
        b".env\nprod\0",
        b".env\tprod\0",
        b"nested/.env.example\0",
        b"state/data.json\0",
        b"nested/runtime/gateway.pid\0",
        b"auth/device.json\0",
        b"auth-profile-secrets/provider.json\0",
        b"secrets/README\0",
        b"credentials/token\0",
        b"sessions/session.json\0",
        b"logs/gateway.log\0",
        b"cache/index\0",
        b"tmp/probe\0",
        b"temp/probe\0",
    ),
)
def test_active_native_tracked_path_classifier_rejects_sensitive_raw_paths(
    payload, tmp_path
):
    result = run_tracked_path_classifier(payload, tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "payload",
    (
        b".env.example",
        b"\0",
        b"README.md\0\0",
    ),
)
def test_active_native_tracked_path_classifier_rejects_invalid_nul_framing(
    payload, tmp_path
):
    result = run_tracked_path_classifier(payload, tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX control-character filenames required")
@pytest.mark.parametrize(
    ("tracked_path", "accepted"),
    (
        pytest.param(".env.example", True, id="root-env-example"),
        pytest.param(".env\nprod", False, id="newline-env"),
        pytest.param(".env\tprod", False, id="tab-env"),
        pytest.param("nested/.env.example", False, id="nested-env-example"),
        pytest.param("state/data.json", False, id="state"),
        pytest.param("nested/runtime/gateway.pid", False, id="runtime"),
        pytest.param("auth/device.json", False, id="auth"),
        pytest.param(
            "auth-profile-secrets/provider.json", False, id="auth-profile-secrets"
        ),
        pytest.param("secrets/README", False, id="secrets"),
        pytest.param("credentials/token", False, id="credentials"),
        pytest.param("sessions/session.json", False, id="sessions"),
        pytest.param("logs/gateway.log", False, id="logs"),
        pytest.param("cache/index", False, id="cache"),
        pytest.param("tmp/probe", False, id="tmp"),
        pytest.param("temp/probe", False, id="temp"),
    ),
)
def test_active_native_tracked_path_classifier_handles_real_git_paths(
    tracked_path, accepted, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(repo)],
        check=True,
        capture_output=True,
    )
    candidate = repo / tracked_path
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"test\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", tracked_path],
        check=True,
        capture_output=True,
    )
    manifest = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout

    result = run_tracked_path_classifier(manifest, tmp_path)

    assert (result.returncode == 0) is accepted
    assert result.stdout == ""
    assert result.stderr == ""


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
