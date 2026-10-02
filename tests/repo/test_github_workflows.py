"""Protect production ordering, trust, and credentials in the three workflows."""

import re
import shlex

import yaml

from tests.helpers import REPO_ROOT


WORKFLOWS = REPO_ROOT / ".github/workflows"


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def commands(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


def step_with(steps, fragment):
    matches = [(index, step) for index, step in enumerate(steps) if fragment in step.get("run", "")]
    assert len(matches) == 1, fragment
    return matches[0]


def test_actions_and_checkouts_are_bound_to_exact_revisions():
    for path in WORKFLOWS.glob("*.yml"):
        source = path.read_text(encoding="utf-8")
        data = yaml.load(source, Loader=yaml.BaseLoader)
        for job in data["jobs"].values():
            for step in job["steps"]:
                if action := step.get("uses"):
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action
                    if action.startswith("actions/checkout@"):
                        assert step["with"]["persist-credentials"] == "false"
                        assert step["with"]["ref"] == "${{ github.sha }}"
        for line in source.splitlines():
            if re.match(r"\s*uses:\s*", line):
                assert re.search(r"\s#\s+v?\d+(?:\.\d+){0,2}\s*$", line), line


def test_production_workflows_share_one_non_cancelling_queue():
    locks = []
    for name in ("apps.yml", "infra.yml"):
        data = workflow(name)
        assert data["permissions"] == {"contents": "read", "id-token": "write"}
        for job in data["jobs"].values():
            assert job["environment"] == "prod"
            lock = job.get("concurrency", data.get("concurrency"))
            assert lock["cancel-in-progress"] == "false"
            assert lock["queue"] == "max"
            locks.append(lock["group"])
    assert len(set(locks)) == 1
    validation = workflow("validate.yml")
    assert validation["permissions"] == {"contents": "read"}
    assert all("environment" not in job for job in validation["jobs"].values())


def test_apps_validate_and_reject_stale_inputs_before_deployment():
    data = workflow("apps.yml")
    job = data["jobs"]["deploy"]
    steps = job["steps"]
    validate, _ = step_with(steps, "python -m pytest")
    bundle, bundling = step_with(steps, "compose_release_engine.py bundle")
    freshness, gate = step_with(steps, "git diff --quiet")
    deploy, activation = step_with(steps, "deploy-release-via-ssh.sh deploy")
    assert validate < bundle < freshness < deploy
    assert "validate-compose.sh" in steps[validate]["run"]
    assert "ansible-playbook" not in commands(job)
    assert "--source-sha" in bundling["run"] and "$GITHUB_SHA" in bundling["run"]
    output = re.search(r"--output\s+(\S+)", bundling["run"]).group(1)
    assert output in activation["run"]
    scope = re.search(
        r"FETCH_HEAD\s+--\s*(.*?)\s*;\s*then", gate["run"], flags=re.DOTALL
    ).group(1)
    paths = set(shlex.split(scope.replace("\\\n", " ")))
    assert paths == {path.removesuffix("/**") for path in data["on"]["push"]["paths"]}
    assert "refs/heads/main" in gate["run"]
    assert "workflow_dispatch" in gate["if"] and "!=" in gate["if"]


def test_secret_rotation_uses_only_the_component_bundle():
    data = workflow("apps.yml")
    steps = data["jobs"]["deploy"]["steps"]
    _, sync = step_with(steps, "deploy-release-via-ssh.sh sync-secrets")
    assert "workflow_dispatch" in sync["if"] and "sync-secrets" in sync["if"]
    assert ".tar" not in sync["run"]
    for fragment in ("compose_release_engine.py bundle", "deploy-release-via-ssh.sh deploy"):
        _, step = step_with(steps, fragment)
        assert "sync-secrets" in step["if"] and "!=" in step["if"]
    source = (WORKFLOWS / "apps.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.([A-Z0-9_]+)", source)) == {
        "APPS_SECRET_BUNDLE", "DEPLOY_SSH_KNOWN_HOSTS", "DEPLOY_SSH_PRIVATE_KEY",
        "TS_AUDIENCE", "TS_OAUTH_CLIENT_ID",
    }
    _, cleanup = step_with(steps, 'rm -f -- "$RUNNER_TEMP/apps.json"')
    assert cleanup["if"] == "always()"


def test_infrastructure_binds_pve_access_before_reconciliation():
    data = workflow("infra.yml")
    inputs = data["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs["unit"]["options"]) == {"pve", "tailnet", "apps-host"}
    assert set(inputs["pve_mode"]["options"]) == {"plan", "audit", "apply"}
    for approval in ("allow_destructive_vmid", "allow_replacement_vmid"):
        assert inputs[approval]["default"] == "" and inputs[approval]["required"] == "false"
    job = data["jobs"]["reconcile"]
    steps = job["steps"]
    configure, _ = step_with(steps, "configure-ssh.sh")
    materialize, pve = step_with(steps, '"component": "pve"')
    bind, binding = step_with(steps, "verify_pve_access_bundle.py")
    reconcile, command = step_with(steps, "ansible-playbook")
    assert configure < materialize < bind < reconcile
    assert pve["if"] == binding["if"]
    assert all(term in binding["if"] for term in ("matrix.unit", "pve", "pve_mode", "apply"))
    assert '"ssh-keygen", "-y"' in pve["run"] and ".ssh/id_ed25519" in pve["run"]
    assert "--private-key" in binding["run"] and "--bundle" in binding["run"]
    _, tailnet = step_with(steps, '"component": "tailnet"')
    assert tailnet["env"]["TAILNET_AUTH_KEY"] == "${{ secrets.TAILSCALE_AUTH_KEY }}"
    assert "destination.chmod(0o600)" in pve["run"] and "destination.chmod(0o600)" in tailnet["run"]
    assert "homelab_unit=$UNIT" in command["run"]
    scheduled, manual = job["strategy"]["matrix"]["unit"].split("||", maxsplit=1)
    assert all('"' + unit + '"' in scheduled for unit in ("tailnet", "apps-host"))
    assert '"pve"' not in scheduled and "inputs.unit" in manual


def test_validation_covers_the_application_and_every_explicit_infrastructure_unit():
    job = workflow("validate.yml")["jobs"]["validate"]
    command = commands(job)
    assert "python -m pytest" in command and "validate-compose.sh" in command
    assert "infra/ansible/playbooks/reconcile.yml" in command
    assert all(unit in command for unit in ("pve", "tailnet", "apps-host"))
    assert "--syntax-check" in command
