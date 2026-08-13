from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from tests.helpers import REPO_ROOT


def load_script(name: str):
    path = REPO_ROOT / "scripts" / "ci" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def foundation_config() -> dict:
    return {
        "secrets": {
            "providers": {
                "gateway_token_file": {
                    "source": "file",
                    "path": "/run/secrets/openclaw_gateway_token",
                    "mode": "singleValue",
                }
            }
        },
        "gateway": {
            "mode": "local",
            "port": 18789,
            "bind": "lan",
            "auth": {
                "mode": "token",
                "token": {
                    "source": "file",
                    "provider": "gateway_token_file",
                    "id": "value",
                },
            },
            "controlUi": {"allowedOrigins": ["http://127.0.0.1:18789"]},
        },
    }


def test_converter_builds_exact_proxy_only_native_foundation():
    module = load_script("prepare-openclaw-native-checkout.py")
    converted = module.converted_config(
        foundation_config(),
        "192.168.0.5",
        "192.168.0.3",
        "https://openclaw.home.hchu.me",
    )

    gateway = converted["gateway"]
    assert gateway == {
        "mode": "local",
        "port": 18789,
        "bind": "custom",
        "customBindHost": "192.168.0.5",
        "auth": {
            "mode": "token",
            "token": {
                "source": "file",
                "provider": "gateway_token_file",
                "id": "value",
            },
            "rateLimit": {
                "maxAttempts": 10,
                "windowMs": 60000,
                "lockoutMs": 300000,
                "exemptLoopback": True,
            },
            "allowTailscale": False,
        },
        "controlUi": {
            "enabled": True,
            "allowedOrigins": ["https://openclaw.home.hchu.me"],
        },
        "trustedProxies": ["192.168.0.3"],
        "allowRealIpFallback": False,
        "tailscale": {"mode": "off", "resetOnExit": False},
        "terminal": {"enabled": False},
    }
    assert converted["secrets"]["providers"]["gateway_token_file"]["path"] == (
        "${OPENCLAW_GATEWAY_TOKEN_FILE}"
    )
    serialized = json.dumps(converted)
    assert "publicOrigin" not in serialized
    assert "/run/secrets/openclaw_gateway_token" not in serialized


@pytest.mark.parametrize("section", ["agents", "models", "channels", "skills"])
def test_converter_rejects_non_foundation_sections(section):
    module = load_script("prepare-openclaw-native-checkout.py")
    config = foundation_config()
    config[section] = {}
    with pytest.raises(ValueError, match="unexpectedly defines"):
        module.converted_config(
            config,
            "192.168.0.5",
            "192.168.0.3",
            "https://openclaw.home.hchu.me",
        )


def test_converter_rejects_secret_shape_drift_and_unsafe_origin():
    module = load_script("prepare-openclaw-native-checkout.py")
    config = foundation_config()
    config["gateway"]["auth"]["token"]["source"] = "secret"
    with pytest.raises(ValueError, match="expected token SecretRef"):
        module.converted_config(
            config,
            "192.168.0.5",
            "192.168.0.3",
            "https://openclaw.home.hchu.me",
        )
    with pytest.raises(ValueError, match="HTTPS origin"):
        module.https_origin("https://openclaw.home.hchu.me/path")


def test_tree_manifest_covers_content_paths_and_links_but_not_modes(tmp_path):
    module = load_script("openclaw-tree-manifest.py")
    source = tmp_path / "source"
    target = tmp_path / "target"
    for root in (source, target):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "value").write_text("same\n", encoding="utf-8")
        try:
            os.symlink("nested/value", root / "link")
        except OSError:
            (root / "link").write_text("link fallback\n", encoding="utf-8")
    os.chmod(source / "nested" / "value", 0o600)
    os.chmod(target / "nested" / "value", 0o644)
    assert module.manifest(source) == module.manifest(target)

    (target / "nested" / "value").write_text("changed\n", encoding="utf-8")
    assert module.manifest(source) != module.manifest(target)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires privileges")
@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside"])
def test_tree_manifest_rejects_root_escaping_links(tmp_path, target):
    module = load_script("openclaw-tree-manifest.py")
    root = tmp_path / "source"
    (root / "nested").mkdir(parents=True)
    os.symlink(target, root / "nested" / "escape")
    with pytest.raises(ValueError, match="symlink target is forbidden"):
        module.manifest(root)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires privileges")
def test_tree_manifest_excludes_only_exact_docker_generated_plugin_skills(tmp_path):
    module = load_script("openclaw-tree-manifest.py")
    source = tmp_path / "source"
    expected = tmp_path / "expected"
    for root in (source, expected):
        root.mkdir()
        (root / "kept").write_text("state\n", encoding="utf-8")

    generated = source / "plugin-skills"
    generated.mkdir()
    for name, target in module.DOCKER_GENERATED_PLUGIN_SKILLS.items():
        os.symlink(target, generated / name)

    with pytest.raises(ValueError, match="absolute symlink target is forbidden"):
        module.manifest(source)
    assert module.manifest(
        source, exclude_docker_generated_plugin_skills=True
    ) == module.manifest(
        expected,
        exclude_docker_generated_plugin_skills=True,
        allow_absent_docker_generated_plugin_skills=True,
    )
    with pytest.raises(ValueError, match="plugin-skills directory is absent"):
        module.manifest(expected, exclude_docker_generated_plugin_skills=True)

    (generated / "canvas").unlink()
    os.symlink("/unexpected/canvas", generated / "canvas")
    with pytest.raises(ValueError, match="plugin-skills link is invalid: canvas"):
        module.manifest(source, exclude_docker_generated_plugin_skills=True)

    (generated / "canvas").unlink()
    os.symlink(
        module.DOCKER_GENERATED_PLUGIN_SKILLS["canvas"], generated / "canvas"
    )
    (generated / "unexpected").write_text("must not be skipped\n", encoding="utf-8")
    with pytest.raises(ValueError, match="entries differ from the allowlist"):
        module.manifest(source, exclude_docker_generated_plugin_skills=True)


def test_migration_excludes_only_validated_ephemeral_plugin_skill_links():
    script = (REPO_ROOT / "scripts" / "ci" / "migrate-openclaw-native.sh").read_text(
        encoding="utf-8"
    )
    source_preflight = script.split("<<'SOURCE_PREFLIGHT'", maxsplit=1)[1].split(
        "SOURCE_PREFLIGHT", maxsplit=1
    )[0]
    final_native_proof = script.split("<<'FINAL_NATIVE_PROOF'", maxsplit=1)[1].split(
        "FINAL_NATIVE_PROOF", maxsplit=1
    )[0]

    assert script.count("--exclude-docker-generated-plugin-skills") == 2
    assert script.count("--allow-absent-docker-generated-plugin-skills") == 1
    assert (
        "python3 '${remote_manifest}' --exclude-docker-generated-plugin-skills "
        "'${source_runtime}/state'" in script
    )
    assert (
        "python3 '${remote_manifest}' --exclude-docker-generated-plugin-skills "
        "--allow-absent-docker-generated-plugin-skills "
        "'${destination_state_stage}'" in script
    )
    assert script.count("--exclude='./plugin-skills'") == 1
    assert "test ! -e '${destination_state_stage}/plugin-skills'" in script
    assert "test ! -L '${destination_state_stage}/plugin-skills'" in script

    expected_links = {
        "browser-automation": "/app/dist/extensions/browser/skills/browser-automation",
        "canvas": "/app/dist/extensions/canvas/skills/canvas",
    }
    assert 'test ! -L "$plugin_skills"' in source_preflight
    assert '"1000:1000 755"' in source_preflight
    assert "-mindepth 1 -maxdepth 1 -printf '%f\\n'" in source_preflight
    for name, target in expected_links.items():
        assert f'test -L "$plugin_skills/{name}"' in source_preflight
        assert target in source_preflight

    assert 'plugin_skills=/var/lib/openclaw/plugin-skills' in final_native_proof
    assert '"1000:1000 700"' in final_native_proof
    assert 'for plugin_skill in browser-automation canvas; do' in final_native_proof
    assert 'case "$plugin_target" in /opt/openclaw/*)' in final_native_proof
    assert 'test -f "$plugin_target/SKILL.md"' in final_native_proof


def test_migration_orchestration_is_explicit_fail_closed_and_secret_quiet():
    script = (REPO_ROOT / "scripts" / "ci" / "migrate-openclaw-native.sh").read_text(
        encoding="utf-8"
    )

    assert script.count("StrictHostKeyChecking=yes") >= 2
    assert "ssh-keyscan" not in script
    assert "scp " not in script
    assert "mkfifo -m 0600" in script
    assert "openclaw-tree-manifest.py" in script
    assert script.index("docker compose stop -t 30") < script.index(
        'source_state_manifest="$(run_ssh'
    )
    assert ".openclaw-setup.migration" in script
    assert ".openclaw-native-migration-owned" in script
    assert "trap 'exit 130' INT" in script
    assert "trap 'exit 143' TERM" in script
    assert "openclaw-migration-native-watchdog" in script
    assert "openclaw-migration-failback" in script
    assert script.index("arm-native-watchdog") < script.index("arm-failback")
    assert script.index("arm-failback") < script.index("docker compose stop -t 30")
    watchdog = (REPO_ROOT / "scripts" / "ci" / "openclaw-native-watchdog.sh").read_text(
        encoding="utf-8"
    )
    failback = (REPO_ROOT / "scripts" / "ci" / "openclaw-docker-failback.sh").read_text(
        encoding="utf-8"
    )
    assert 'expired="${guard_root}/native-watchdog.expired"' in watchdog
    assert 'while test -e "${armed}"; do' in watchdog
    assert "systemctl mask --runtime --now openclaw-gateway.service" in watchdog
    watchdog_mask = watchdog.index("systemctl mask --runtime --now openclaw-gateway.service")
    watchdog_persistent_disable = watchdog.index(
        "multi-user.target.wants/openclaw-gateway.service"
    )
    watchdog_disabled_proof = watchdog.index(
        "! systemctl is-enabled --quiet openclaw-gateway.service"
    )
    watchdog_listener_proof = watchdog.index("! ss -H -ltn 'sport = :18789'")
    assert watchdog_mask < watchdog_persistent_disable < watchdog_disabled_proof
    assert watchdog_disabled_proof < watchdog_listener_proof
    assert 'test ! -e "${native_enable}"' in watchdog
    assert 'test ! -L "${native_enable}"' in watchdog
    assert "/proc/sys/kernel/random/boot_id" in watchdog
    assert 'while test -e "${armed}" || test -L "${armed}"; do' in failback
    assert "stop_and_prove_old_gateway" in failback
    assert "docker compose stop -t 30 openclaw-gateway" in failback
    assert "start_and_prove_old_gateway" in failback
    assert 'force_path="${guard_root}/failback.force"' in failback
    assert failback.index('rm -f -- "${armed}"') < failback.index(
        'rm -f -- "${force_path}"'
    )
    prepare_source = script.index("prepare-persistent-source-failback")
    stop_destination = script.index("stop-native-for-failback")
    authorize_source = script.index("authorize-persistent-failback")
    assert prepare_source < stop_destination < authorize_source
    source_fence = script.split("<<'PREPARE_SOURCE_FAILBACK'", maxsplit=1)[1].split(
        "PREPARE_SOURCE_FAILBACK", maxsplit=1
    )[0]
    stop_native = script.split("<<'STOP_NATIVE'", maxsplit=1)[1].split(
        "STOP_NATIVE", maxsplit=1
    )[0]
    forced_restore = script.split("<<'ROLLBACK'", maxsplit=1)[1].split(
        "ROLLBACK", maxsplit=1
    )[0]
    authorization_line = next(
        line.strip() for line in script.splitlines() if "<<'ROLLBACK'" in line
    )
    authorization_prefix = 'if ! run_ssh "${source_target}" "'
    authorization_suffix = '" <<\'ROLLBACK\''
    assert authorization_line.startswith(authorization_prefix)
    assert authorization_line.endswith(authorization_suffix)
    authorization_argv = authorization_line[
        len(authorization_prefix) : -len(authorization_suffix)
    ].split()
    assert authorization_argv == [
        "sh",
        "-s",
        "--",
        "authorize-persistent-failback",
        "${rollback_state}",
    ]
    assert 'operation="$1"' in forced_restore
    assert 'requested_state="$2"' in forced_restore
    assert 'test "$operation" = authorize-persistent-failback' in forced_restore
    assert 'atomic_guard_write "$marker_value" "$armed"' in source_fence
    assert "source_fence_proven=1" in source_fence
    assert 'printf \'%s\\n\' fenced' in source_fence
    assert "systemctl stop openclaw-migration-failback.service" not in source_fence
    assert source_fence.index('if test "$force_present" -eq 1') < source_fence.index(
        "systemctl start openclaw-migration-failback.service"
    )
    assert "multi-user.target.wants/openclaw-gateway.service" in stop_native
    assert (
        "test ! -e /etc/systemd/system/multi-user.target.wants/"
        "openclaw-gateway.service" in stop_native
    )
    assert "! systemctl is-enabled --quiet openclaw-gateway.service" in stop_native
    fenced_authorization = forced_restore.split('  1:0)', maxsplit=1)[1].split(
        '  1:1)', maxsplit=1
    )[0]
    assert 'mv -f -- "$force_stage" "$force"' in fenced_authorization
    assert '"$deadline"' not in fenced_authorization
    assert '"$boot_id"' not in fenced_authorization
    assert '"$armed"' not in fenced_authorization
    assert "systemctl restart" not in fenced_authorization
    assert 'ln "$force" "$armed"' in forced_restore
    assert 'test "$force" -ef "$armed"' in forced_restore
    force_only = forced_restore.split('  0:1)', maxsplit=1)[1].split(
        '  *) exit 64', maxsplit=1
    )[0]
    assert force_only.index("old_gateway_ready") < force_only.index('rm -f -- "$force"')
    assert "systemctl restart openclaw-migration-failback.service" in forced_restore
    assert "systemctl enable openclaw-migration-failback.service" in forced_restore
    assert "docker compose start openclaw-gateway" in failback
    assert 'boot_id_path="${guard_root}/failback.boot-id"' in failback
    assert script.index("prove-native-lease") < script.index(
        "--extra-vars openclaw_native_activate=true"
    )
    assert script.rindex("disarm-native-watchdog") < script.rindex("disarm-failback")
    assert "git_safe diff --cached --no-ext-diff --no-textconv --name-only" in script
    assert 'git_safe grep --quiet -F -f "$token_pattern"' in script
    assert 'grep -Fq -f "$token_pattern"' in script
    assert 'gateway_token="' not in script
    assert "worktree|editor|askpass|sshcommand|gitproxy|pager" in script
    assert 'test ! -e "$setup/.gitmodules"' in script
    assert "tracked_token_status" in script
    assert "cached_token_status" in script
    assert 'test "$tracked_token_status" -eq 1' in script
    assert 'test "$cached_token_status" -eq 1' in script
    assert "secrets audit --check --json >/dev/null" in script
    assert "OPENCLAW_SUPERVISOR_MODE=external" in script
    assert "curl -fsS http://192.168.0.5:18789/readyz" in script
    assert "OPENCLAW_GATEWAY_TOKEN=" not in script
    assert "OPENCLAW_GATEWAY_TOKEN_FILE=" in script
    https_proof = script.index("https://openclaw.home.hchu.me/healthz")
    destination_marker = script.index("mark-validated-native")
    source_marker = script.index("mark-validated-source")
    assert https_proof < destination_marker < source_marker
    assert source_marker < script.rindex("disarm-native-watchdog")
    assert "finalize-openclaw-native-cutover.yml" in script
    assert "systemctl is-active --quiet openclaw-migration-failback.service" in script
    assert "recoverable_state_detected=0" in script
    assert script.count("recoverable_state_detected=1") == 2
    assert '[ "${recoverable_state_detected}" -eq 1 ]' in script
    assert '[ "${result}" -eq 1 ]' in script

    paired_recovery = script.split(
        'if [ "${destination_marker_state}" = valid ]', maxsplit=1
    )[1].split('case "${destination_marker_state}:${source_marker_state}"', maxsplit=1)[0]
    paired_release = paired_recovery.index("release-import-ownership")
    assert paired_recovery.index("old_gateway_stopped=0") < paired_release
    assert paired_recovery.index("failback_armed=0") < paired_release

    normal_commit = script.rsplit("DISARM_FAILBACK", maxsplit=1)[1]
    normal_release = normal_commit.index("release-import-ownership")
    assert normal_commit.index("old_gateway_stopped=0") < normal_release
    assert normal_commit.index("failback_armed=0") < normal_release

    force_branch = failback.index('if test -e "${force_path}" || test -L "${force_path}"')
    force_return = failback.index("return 0", force_branch)
    unconditional_fence = failback.index("stop_and_prove_old_gateway", force_return)
    assert force_branch < force_return < unconditional_fence
    no_force_body = failback[force_return:]
    assert "start_and_prove_old_gateway" not in no_force_body
    assert "http://192.168.0.5:18789" not in failback
    assert 'test "${current_epoch}"' not in failback
    assert 'date +%s' not in failback
    assert 'rm -f -- "${armed}"' not in no_force_body
    assert "one behavior: continuously fence old" in failback


def test_failback_verification_retries_until_readiness_and_marker_cleanup_are_atomic(
    tmp_path,
):
    script = (REPO_ROOT / "scripts" / "ci" / "migrate-openclaw-native.sh").read_text(
        encoding="utf-8"
    )
    forced_restore = script.split("<<'ROLLBACK'", maxsplit=1)[1].split(
        "ROLLBACK", maxsplit=1
    )[0]
    poll_start = forced_restore.index("for attempt in $(seq 1 90); do")
    poll_end = forced_restore.index("exit 1", poll_start) + len("exit 1")
    poll = forced_restore[poll_start:poll_end]

    assert 'if old_gateway_ready \\\n      && test ! -e "$armed"' in poll
    assert '&& test ! -L "$force"; then' in poll

    shell = shutil.which("sh")
    if not shell:
        shell = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
        )
    if not os.path.exists(shell):
        pytest.skip("POSIX shell is unavailable")

    test_root = str(tmp_path)
    if os.name == "nt":
        drive, remainder = os.path.splitdrive(test_root)
        test_root = f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"
    harness = f'''set -eu
test_root="$1"
armed="$test_root/failback.armed"
force="$test_root/failback.force"
touch "$armed" "$force"
old_gateway_ready() {{ return 0; }}
sleep() {{ rm -f -- "$armed" "$force"; }}
{poll}
'''
    result = subprocess.run(
        [shell, "-c", harness, "failback-poll", test_root],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert not (tmp_path / "failback.armed").exists()
    assert not (tmp_path / "failback.force").exists()


def test_initial_migration_guards_publish_armed_only_after_persistent_prerequisites():
    script = (REPO_ROOT / "scripts" / "ci" / "migrate-openclaw-native.sh").read_text(
        encoding="utf-8"
    )
    native_arm = script.split("<<'ARM_NATIVE_WATCHDOG'", maxsplit=1)[1].split(
        "ARM_NATIVE_WATCHDOG", maxsplit=1
    )[0]
    source_arm = script.split("<<'ARM_FAILBACK'", maxsplit=1)[1].split(
        "ARM_FAILBACK", maxsplit=1
    )[0]

    transactions = (
        (
            native_arm,
            'mv -f -- "$unit_stage" "$unit"',
            'atomic_guard_write "$(( $(date +%s) + 2700 ))" "$deadline_path"',
            'atomic_guard_write "$(cat /proc/sys/kernel/random/boot_id)" "$boot_id_path"',
            "systemctl enable openclaw-migration-native-watchdog.service",
            'atomic_guard_write "$marker_value" "$armed"',
            "systemctl start openclaw-migration-native-watchdog.service",
        ),
        (
            source_arm,
            'mv -f -- "$unit_stage" "$unit"',
            'atomic_guard_write "$(( $(date +%s) + 3000 ))" "$deadline_path"',
            'atomic_guard_write "$(cat /proc/sys/kernel/random/boot_id)" "$boot_id_path"',
            "systemctl enable openclaw-migration-failback.service",
            'atomic_guard_write "$marker_value" "$armed"',
            "systemctl start openclaw-migration-failback.service",
        ),
    )
    for block, unit, deadline, boot_id, enable, armed, start in transactions:
        # Every SIGKILL prefix before `armed` is uncommitted; every prefix after
        # it already has an installed unit, durable metadata, and boot enable.
        positions = [block.index(token) for token in (unit, deadline, boot_id, enable, armed, start)]
        assert positions == sorted(positions)
        assert "enable --now openclaw-migration" not in block
        assert 'printf \'%s\\n\' homelab-openclaw-native-migration-v1 >' not in block
        assert 'test ! -L "$guard_path"' in block
        assert 'test "$(stat -c \'%u:%g %a\' "$guard_path")" = "0:0 600"' in block

    assert '"$installed_helper" once' in native_arm
    assert "docker compose ps --status running -q openclaw-gateway" in source_arm
    assert "systemctl is-active --quiet openclaw-migration-failback.service" in source_arm


def test_cleanup_recovers_only_structurally_safe_guard_transaction_prefixes():
    script = (REPO_ROOT / "scripts" / "ci" / "migrate-openclaw-native.sh").read_text(
        encoding="utf-8"
    )
    prepare_source = script.split("<<'PREPARE_SOURCE_FAILBACK'", maxsplit=1)[1].split(
        "PREPARE_SOURCE_FAILBACK", maxsplit=1
    )[0]
    stop_native = script.split("<<'STOP_NATIVE'", maxsplit=1)[1].split(
        "STOP_NATIVE", maxsplit=1
    )[0]
    disarm_native = script.split("<<'DISARM_ROLLBACK_NATIVE'", maxsplit=1)[1].split(
        "DISARM_ROLLBACK_NATIVE", maxsplit=1
    )[0]

    # Source normalization happens while native is untouched and accepts an
    # older root-owned armed-first prefix without trusting its content/mode.
    assert 'test "$(stat -c \'%u:%g %a\' "$guard_root")" = "0:0 700"' in prepare_source
    assert 'test "$(stat -c \'%u:%g\' "$guard_path")" = "0:0"' in prepare_source
    normalizer = prepare_source.split(
        "# With no rollback authorization", maxsplit=1
    )[1]
    assert normalizer.index('atomic_guard_write "$(( $(date +%s) + 3000 ))"') < normalizer.index(
        'atomic_guard_write "$marker_value" "$armed"'
    )
    assert normalizer.index("systemctl enable openclaw-migration-failback.service") < normalizer.index(
        'atomic_guard_write "$marker_value" "$armed"'
    )
    assert normalizer.index('atomic_guard_write "$marker_value" "$armed"') < normalizer.index(
        "systemctl start openclaw-migration-failback.service"
    )
    assert 'validate_marker "$force"' in prepare_source
    assert prepare_source.index('if test "$force_present" -eq 1') < prepare_source.index(
        "# With no rollback authorization"
    )
    assert '.failback.guard.*' in prepare_source
    assert '.failback.force.*' in prepare_source

    # Destination prefixes are normalized only after persistent native disable
    # proof and before the runtime mask is released.
    assert "multi-user.target.wants/openclaw-gateway.service" in stop_native
    assert 'test "$(stat -c \'%u:%g\' "$guard_path")" = "0:0"' in disarm_native
    assert disarm_native.index("systemctl disable --now openclaw-migration-native-watchdog.service") < disarm_native.index(
        "/var/lib/openclaw-migration/native-watchdog.armed"
    )
    assert disarm_native.index("/var/lib/openclaw-migration/native-watchdog.deadline") < disarm_native.index(
        "systemctl unmask --runtime openclaw-gateway.service"
    )
    assert ".native-watchdog.guard.*" in disarm_native

    destination_stale = script.split("<<'DESTINATION_STALE_STATE'", maxsplit=1)[1].split(
        "DESTINATION_STALE_STATE", maxsplit=1
    )[0]
    source_stale = script.split("<<'SOURCE_STALE_STATE'", maxsplit=1)[1].split(
        "SOURCE_STALE_STATE", maxsplit=1
    )[0]
    assert ".native-watchdog.guard.*" in destination_stale
    assert ".failback.guard.*" in source_stale
    assert ".failback.force.*" in source_stale


def test_cd_migration_is_dispatch_only_after_narrow_stage_validation():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    docker_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_docker_apps.yml"
    ).read_text(encoding="utf-8")
    assert "migrate_openclaw_native:" in workflow
    recovery_step = workflow.split(
        "- name: Recover an interrupted native OpenClaw migration", maxsplit=1
    )[1].split("      - name:", maxsplit=1)[0]
    assert "github.event_name == 'workflow_dispatch'" in recovery_step
    assert "inputs.migrate_openclaw_native == true" in recovery_step
    assert "migrate-openclaw-native.sh recover-only" in recovery_step
    migration_step = workflow.split(
        "- name: Transfer and activate native OpenClaw", maxsplit=1
    )[1].split("      - name:", maxsplit=1)[0]
    assert "github.event_name == 'workflow_dispatch'" in migration_step
    assert "inputs.migrate_openclaw_native == true" in migration_step
    assert "steps.scope.outputs.deployment_scope == 'full'" in migration_step
    assert "migrate-openclaw-native.sh" in migration_step
    assert workflow.index("- name: Validate the narrow native OpenClaw stage") < workflow.index(
        "- name: Transfer and activate native OpenClaw"
    )
    assert workflow.index(
        "- name: Recover an interrupted native OpenClaw migration"
    ) < workflow.index("- name: Stage only native OpenClaw and its Traefik route")
    assert workflow.index("- name: Transfer and activate native OpenClaw") < workflow.index(
        "- name: Prove unrelated transition workloads retained their identities"
    )
    assert 'OPENCLAW_NATIVE_TRANSITION: "true"' in workflow
    assert "Require the automatic transition identity proof to pass" in workflow


def test_native_activation_phase_and_one_shot_migration_workflow_are_coupled():
    phase_vars = yaml.safe_load((
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_openclaw.yml"
    ).read_text(encoding="utf-8"))
    docker_vars = yaml.safe_load((
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_docker_apps.yml"
    ).read_text(encoding="utf-8"))
    platform = next(
        project
        for project in docker_vars["docker_compose_projects"]
        if project["name"] == "platform"
    )
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    selector = (
        REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py"
    ).read_text(encoding="utf-8")
    selector_module = load_script("select-deployment-scope.py")
    all_vars = yaml.safe_load((
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "all.yml"
    ).read_text(encoding="utf-8"))
    route = yaml.safe_load((
        REPO_ROOT
        / "apps"
        / "compose"
        / "platform"
        / "dynamic"
        / "routes.yml"
    ).read_text(encoding="utf-8"))
    assert not (
        REPO_ROOT
        / "apps"
        / "compose"
        / "platform"
        / "dynamic"
        / "routes.yml.j2"
    ).exists()
    route_url = route["http"]["services"]["openclaw"]["loadBalancer"]["servers"][0]["url"]
    migration_surface = (
        "migrate_openclaw_native",
        "MIGRATE_OPENCLAW_NATIVE",
        "OPENCLAW_NATIVE_TRANSITION",
        "OPENCLAW_NATIVE_STAGE_ONLY",
        "migrate-openclaw-native.sh",
        "bootstrap-openclaw-native.yml",
        "trust-openclaw-native.yml",
        "stage-openclaw-native.yml",
        "validate-openclaw-native-stage.yml",
        "transition_identity",
        "TRANSITION_IDENTITY",
        "RETAINED_GATEWAY_BASELINE",
        "RETAINED_GATEWAY_CURRENT",
        "Capture unaffected transition workload identities",
        "Prove unrelated transition workloads retained their identities",
    )

    transition_lane_present = 'OPENCLAW_NATIVE_TRANSITION: "true"' in workflow
    assert not (
        phase_vars["openclaw_native_activate"]
        and all_vars["openclaw_docker_rollback_activate"]
    )

    if not transition_lane_present:
        assert all(token not in workflow for token in migration_surface)
        assert "runtime_file_force_recreate_services" not in platform
        assert '"openclaw": "apps/compose/openclaw/"' not in selector
        assert selector_module["classify_paths"](
            ["apps/compose/openclaw/compose.yml"]
        ) == "full"
        assert selector_module["select_arcane_projects"](
            ["apps/compose/openclaw/compose.yml"]
        ) == []
        if all_vars["openclaw_docker_rollback_activate"] is True:
            assert phase_vars["openclaw_native_activate"] is False
            assert route_url == "http://openclaw-rollback:18789"
        else:
            assert phase_vars["openclaw_native_activate"] is True
            assert route_url == "http://192.168.0.5:18789"
        assert "Fence retained Docker OpenClaw before native reconciliation" in workflow
        assert workflow.index(
            "Fence retained Docker OpenClaw before native reconciliation"
        ) < workflow.index("- name: Deploy services")
        for ordinary_step in (
            "Deploy changed workloads with Arcane",
            "OpenTofu plan",
            "OpenTofu apply",
            "Bootstrap Proxmox and LXC access",
            "Deploy services",
            "Validate services",
        ):
            assert f"- name: {ordinary_step}" in workflow
    else:
        assert phase_vars["openclaw_native_activate"] is False
        assert all(token in workflow for token in migration_surface)
        assert platform["runtime_file_force_recreate_services"] == ["traefik"]
        assert '"openclaw": "apps/compose/openclaw/"' in selector
        assert all_vars["openclaw_docker_rollback_activate"] is False
        assert route_url == "http://192.168.0.5:18789"

    docker_role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "openclaw_foundation"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    native_role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "openclaw_native"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    arcane_role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "arcane_manager"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    assert "native-cutover-validated" in docker_role
    assert "Preserve the validated native cutover and hold Docker Gateway stopped" in docker_role
    assert "Remove the completed native OpenClaw transition marker" in native_role
    assert "state: absent" in native_role.split(
        "Remove the completed native OpenClaw transition marker", 1
    )[1]
    assert "Select marker-aware Arcane GitOps projects" in arcane_role
    assert "rejectattr('name', 'equalto', 'openclaw')" in arcane_role
