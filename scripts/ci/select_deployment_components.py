#!/usr/bin/env python3
"""Fail-closed component, release, and immutable-image selection."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable

try:
    from .immutable_image_release import ContractError, validate_immutable_ref
except ImportError:  # pragma: no cover - direct workflow execution
    from immutable_image_release import ContractError, validate_immutable_ref


COMPONENT_ORDER = ("tofu", "bootstrap", "tailnet", "openclaw", "apps")
APP_PROJECT_ORDER = ("homelab",)
IMAGE_BUILD_ORDER = ("openclaw_gateway", "openclaw_ctf", "t3code")
ALL_COMPONENTS = frozenset(COMPONENT_ORDER)
ALL_APP_PROJECTS = frozenset(APP_PROJECT_ORDER)
SHA = re.compile(r"[0-9a-f]{40}")
BARE_SHA256 = re.compile(r"[0-9a-f]{64}")
NO_DEPLOYED_REVISION = "0" * 40


@dataclass(frozen=True)
class Ownership:
    components: frozenset[str] = frozenset()
    apps_projects: frozenset[str] = frozenset()
    image_builds: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DeploymentSelection:
    components: tuple[str, ...]
    apps_projects: tuple[str, ...] = ()
    openclaw_setup_commit: str = ""
    openclaw_gateway_ref: str = ""
    openclaw_ctf_ref: str = ""
    openclaw_runtime_sha256: str = ""
    openclaw_config_sha256: str = ""
    image_builds: tuple[str, ...] = ()


class SelectionError(RuntimeError):
    """The event cannot identify one safe production action."""


class UnownedPathsError(SelectionError):
    def __init__(self, paths: Iterable[str]):
        self.paths = tuple(sorted(set(paths)))
        super().__init__(
            "Unowned deployment paths:\n"
            + "\n".join(f"  {path}" for path in self.paths)
            + "\nAdd an explicit ownership rule before deployment."
        )


def owner(
    components: Iterable[str] = (),
    apps_projects: Iterable[str] = (),
    image_builds: Iterable[str] = (),
) -> Ownership:
    return Ownership(
        frozenset(components), frozenset(apps_projects), frozenset(image_builds)
    )


ALL_SERVICES = frozenset(("bootstrap", "tailnet", "openclaw", "apps"))
TOFU_BOOTSTRAP = frozenset(("tofu", "bootstrap"))


EXACT_OWNERS: dict[str, Ownership] = {
    ".github/workflows/ci.yml": owner(),
    ".github/workflows/cd.yml": owner(),
    ".opentofu-version": owner(TOFU_BOOTSTRAP),
    "requirements-deploy.txt": owner(),
    "infra/ansible/ansible.cfg": owner(("bootstrap",)),
    "infra/ansible/requirements.yml": owner(("bootstrap",)),
    "infra/ansible/files/validate-vuetorrent.sh": owner(),
    "infra/ansible/inventory/prod/topology.json": owner(TOFU_BOOTSTRAP),
    "infra/ansible/inventory/prod/group_vars/all.yml": owner(ALL_SERVICES),
    "infra/ansible/inventory/prod/group_vars/debian.yml": owner(("bootstrap",)),
    "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml": owner(("bootstrap", "apps"), ALL_APP_PROJECTS),
    "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml": owner(("bootstrap",)),
    "infra/ansible/inventory/prod/group_vars/svc_tailnet.yml": owner(("tailnet",)),
    "infra/ansible/playbooks/bootstrap.yml": owner(("bootstrap",)),
    "infra/ansible/playbooks/site.yml": owner(ALL_SERVICES, ALL_APP_PROJECTS),
    "infra/ansible/playbooks/validate.yml": owner(),
    "infra/ansible/playbooks/maintenance.yml": owner(),
    "infra/ansible/playbooks/preflight-openclaw-lxc.yml": owner(TOFU_BOOTSTRAP),
    "infra/ansible/playbooks/trust-openclaw-lxc.yml": owner(("bootstrap",)),
    "infra/ansible/playbooks/trust-docker-apps.yml": owner(("bootstrap",)),
    "infra/deployment/secrets.json": owner(ALL_SERVICES, ALL_APP_PROJECTS),
    "scripts/recovery/compose_stack_cutover.py": owner(),
}


# Deletions in the architecture cutover are explicitly empty-owned so the
# adoption diff stays fail-closed without reintroducing legacy projects.
RETIRED_PREFIXES = (
    "apps/compose/platform/",
    "apps/compose/media/",
    "apps/compose/code/",
    "apps/compose/openclaw/",
    "infra/ansible/roles/openclaw_foundation/",
    "infra/ansible/roles/openclaw_ctf_local_docker/",
    "infra/ansible/roles/openclaw_traefik_route/",
)
RETIRED_EXACT = frozenset(
    (
        "infra/ansible/playbooks/fence-openclaw-docker-before-native.yml",
        "infra/ansible/playbooks/fence-openclaw-retained-assets.yml",
        "infra/ansible/playbooks/rebaseline-openclaw-retained-rollback.yml",
        "scripts/ci/openclaw-docker-failback.sh",
        "scripts/ci/openclaw-native-watchdog.sh",
    )
)

# Temporary, explicit tombstones for every strict-tree deletion in the
# architecture-adoption commit.  They let the first push classify with
# --no-renames without turning retired code into a deployment trigger.  Remove
# this set only after the adoption commit is the diff base in every environment.
ADOPTION_RETIREMENT_PATHS = frozenset(
    (
        "apps/__init__.py",
        "apps/compose/arcane/.env.example",
        "apps/compose/arcane/README.md",
        "apps/compose/arcane/compose.yml",
        "apps/compose/code/.env.example",
        "apps/compose/code/Dockerfile",
        "apps/compose/code/README.md",
        "apps/compose/code/compose.yml",
        "apps/compose/media/.env.example",
        "apps/compose/media/README.md",
        "apps/compose/media/compose.yml",
        "apps/compose/openclaw/.env.example",
        "apps/compose/openclaw/README.md",
        "apps/compose/openclaw/compose.yml",
        "apps/compose/platform/.env.example",
        "apps/compose/platform/README.md",
        "apps/compose/platform/compose.yml",
        "apps/compose/platform/dynamic/routes.yml",
        "apps/compose/platform/traefik.yml",
        "apps/minecraft/__init__.py",
        "apps/minecraft/scripts/__init__.py",
        "infra/ansible/files/assert-no-game-compose-containers.sh",
        "infra/ansible/inventory/prod/hosts.yml",
        "infra/ansible/playbooks/fence-openclaw-docker-before-native.yml",
        "infra/ansible/playbooks/fence-openclaw-retained-assets.yml",
        "infra/ansible/playbooks/rebaseline-openclaw-retained-rollback.yml",
        "infra/ansible/roles/arcane_manager/tasks/main.yml",
        "infra/ansible/roles/arcane_manager/templates/arcane.env.j2",
        "infra/ansible/roles/docker_compose_project/templates/media.env.j2",
        "infra/ansible/roles/docker_compose_project/templates/platform.env.j2",
        "infra/ansible/roles/docker_compose_project/templates/t3code.env.j2",
        "infra/ansible/roles/openclaw_ctf_local_docker/files/Dockerfile",
        "infra/ansible/roles/openclaw_ctf_local_docker/handlers/main.yml",
        "infra/ansible/roles/openclaw_ctf_local_docker/tasks/main.yml",
        "infra/ansible/roles/openclaw_ctf_local_docker/templates/20-openclaw-ctf-firewall.conf.j2",
        "infra/ansible/roles/openclaw_ctf_local_docker/templates/daemon.json.j2",
        "infra/ansible/roles/openclaw_ctf_local_docker/templates/openclaw-ctf-docker-firewall.sh.j2",
        "infra/ansible/roles/openclaw_foundation/files/openclaw_retained_gateway.py",
        "infra/ansible/roles/openclaw_foundation/tasks/main.yml",
        "infra/ansible/roles/openclaw_foundation/templates/openclaw.env.j2",
        "infra/ansible/roles/openclaw_native/files/classify_openclaw_journal.py",
        "infra/ansible/roles/openclaw_native/files/materialize_openclaw_credential.py",
        "infra/ansible/roles/openclaw_native/files/patch-openclaw-codex-sandbox-cwd.py",
        "infra/ansible/roles/openclaw_native/files/patch-openclaw-discord-autothread-queue.py",
        "infra/ansible/roles/openclaw_native/tasks/classify_gateway_journal.yml",
        "infra/ansible/roles/openclaw_native/templates/openclaw-credential-probe.service.j2",
        "infra/ansible/roles/openclaw_native/templates/openclaw-gateway.service.j2",
        "infra/ansible/roles/openclaw_traefik_route/tasks/main.yml",
        "infra/opentofu/envs/prod/containers.auto.tfvars",
        "scripts/ci/deploy-with-arcane.py",
        "scripts/ci/homelab_topology.py",
        "scripts/ci/openclaw-docker-failback.sh",
        "scripts/ci/openclaw-native-watchdog.sh",
        "scripts/ci/reconcile-arcane.py",
        "scripts/ci/render_ansible_inventory.py",
        "scripts/ci/render_ansible_targets.py",
        "scripts/ci/run-ansible-parallel.sh",
        "scripts/ci/select-deployment-scope.py",
    )
)


ROLE_OWNERS: dict[str, Ownership] = {
    "common_debian": owner(("bootstrap",)),
    "docker_compose_project": owner(("bootstrap", "apps"), ALL_APP_PROJECTS),
    "docker_engine": owner(("bootstrap",)),
    "openclaw_native": owner(("bootstrap",)),
    "pve_homelab_storage": owner(("bootstrap",)),
    "pve_lxc_access_bootstrap": owner(("bootstrap",)),
    "pve_lxc_root_options": owner(("bootstrap",)),
    "tailscale_gateway": owner(("tailnet",)),
}


SCRIPT_OWNERS: dict[str, Ownership] = {
    "check_tofu_plan_safe.py": owner(TOFU_BOOTSTRAP),
    "configure-ssh.sh": owner(),
    "deploy-compose-via-ssh.sh": owner(("apps",), ALL_APP_PROJECTS),
    "deploy_compose_release.py": owner(("apps",), ALL_APP_PROJECTS),
    "deploy-openclaw-via-ssh.sh": owner(("openclaw",)),
    "deploy_openclaw_release.py": owner(("bootstrap", "openclaw")),
    # Shared by both direct deployment kernels. OpenClaw installs this helper
    # during bootstrap, while the app uploader ships it with each release.
    "immutable_image_release.py": owner(
        ("bootstrap", "openclaw", "apps"), ALL_APP_PROJECTS
    ),
    "install-opentofu.sh": owner(TOFU_BOOTSTRAP),
    "install-tools.sh": owner(),
    "openclaw_release.py": owner(("bootstrap", "openclaw")),
    "preflight-openclaw-lxc.py": owner(TOFU_BOOTSTRAP),
    "refresh-lxc-ssh-trust.sh": owner(),
    "select_deployment_components.py": owner(),
    "tofu-apply.sh": owner(TOFU_BOOTSTRAP),
    "tofu-plan.sh": owner(TOFU_BOOTSTRAP),
    "validate-compose.sh": owner(),
    "verify-compose-container-identities.sh": owner(),
    "write_ansible_extra_vars.py": owner(ALL_SERVICES, ALL_APP_PROJECTS),
    "write_tofu_vars.py": owner(TOFU_BOOTSTRAP),
}


STRICT_PREFIXES = ("apps/", "infra/", "scripts/ci/")


def _ordered(values: Iterable[str], order: tuple[str, ...]) -> tuple[str, ...]:
    selected = frozenset(values)
    return tuple(item for item in order if item in selected)


def _docs_or_tests(path: str) -> bool:
    return path.startswith(("docs/", "tests/")) or path == "README.md" \
        or path.endswith("/README.md")


def ownership_for_path(path: str) -> Ownership | None:
    if (
        path in ADOPTION_RETIREMENT_PATHS
        or path in RETIRED_EXACT
        or path.startswith(RETIRED_PREFIXES)
    ):
        return owner()
    if path in EXACT_OWNERS:
        return EXACT_OWNERS[path]
    if _docs_or_tests(path):
        return owner()
    if path == "apps/README.md":
        return owner()
    if path.startswith("apps/compose/homelab/"):
        return owner(("apps",), ALL_APP_PROJECTS)
    if path.startswith("apps/images/t3code/"):
        return owner(("apps",), ALL_APP_PROJECTS, ("t3code",))
    if path.startswith("infra/openclaw/gateway/"):
        return owner(image_builds=("openclaw_gateway",))
    if path.startswith("infra/openclaw/ctf/"):
        return owner(image_builds=("openclaw_ctf",))
    if path.startswith("infra/openclaw/runtime/"):
        return owner(("openclaw",))
    if path == "infra/openclaw/README.md":
        return owner()
    if path.startswith(("infra/opentofu/envs/prod/", "infra/opentofu/modules/pve-lxc/")):
        return owner(TOFU_BOOTSTRAP)
    role_prefix = "infra/ansible/roles/"
    if path.startswith(role_prefix):
        role = path[len(role_prefix):].partition("/")[0]
        if role in ROLE_OWNERS:
            return ROLE_OWNERS[role]
    if path.startswith("scripts/ci/"):
        script = path[len("scripts/ci/"):]
        if "/" not in script:
            return SCRIPT_OWNERS.get(script)
    if path.startswith(STRICT_PREFIXES):
        return None
    return owner()


def classify_paths(paths: Iterable[str]) -> DeploymentSelection:
    components: set[str] = set()
    projects: set[str] = set()
    builds: set[str] = set()
    unowned: list[str] = []
    for raw in paths:
        path = raw.strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        ownership = ownership_for_path(path)
        if ownership is None:
            unowned.append(path)
            continue
        components.update(ownership.components)
        projects.update(ownership.apps_projects)
        builds.update(ownership.image_builds)
    if unowned:
        raise UnownedPathsError(unowned)
    if "t3code" in builds:
        components.add("apps")
        projects.add("homelab")
    if builds.intersection(("openclaw_gateway", "openclaw_ctf")):
        # A changed image input is promoted by this exact workflow run and must
        # reach the existing immutable runtime; it is not merely a CI-only build.
        components.add("openclaw")
    if "apps" in components:
        projects = {"homelab"}
    elif projects:
        raise AssertionError("app project emitted without apps component")
    return DeploymentSelection(
        components=_ordered(components, COMPONENT_ORDER),
        apps_projects=_ordered(projects, APP_PROJECT_ORDER),
        image_builds=_ordered(builds, IMAGE_BUILD_ORDER),
    )


def _require_sha(value: str, field: str) -> str:
    normalized = value.lower()
    if SHA.fullmatch(normalized) is None or set(normalized) == {"0"}:
        raise SelectionError(f"{field} must be an exact nonzero lowercase 40-hex Git SHA")
    return normalized


def _require_hash(value: str, field: str) -> str:
    normalized = value.lower()
    if BARE_SHA256.fullmatch(normalized) is None or set(normalized) == {"0"}:
        raise SelectionError(f"{field} must be an exact nonzero lowercase SHA-256")
    return normalized


def _release_fields(
    prefix: str,
    *,
    allow_computed_runtime: bool = False,
    allow_computed_config: bool = False,
    selected_image_builds: Iterable[str] = (),
) -> dict[str, str]:
    def env(suffix: str) -> str:
        return os.environ.get(f"{prefix}_{suffix}", "")

    builds = frozenset(selected_image_builds)

    def image_ref(suffix: str, build: str) -> str:
        value = env(suffix)
        if not value and build in builds:
            return ""
        return validate_immutable_ref(value)

    try:
        gateway = image_ref("GATEWAY_REF", "openclaw_gateway")
        ctf = image_ref("CTF_REF", "openclaw_ctf")
    except ContractError as exc:
        raise SelectionError(f"OpenClaw image promotion is invalid: {exc}") from exc
    runtime = env("RUNTIME_SHA256")
    config_hash = env("CONFIG_SHA256")
    return {
        "openclaw_setup_commit": _require_sha(env("CONFIG_COMMIT"), f"{prefix}_CONFIG_COMMIT"),
        "openclaw_gateway_ref": gateway,
        "openclaw_ctf_ref": ctf,
        "openclaw_runtime_sha256": (
            "" if allow_computed_runtime and not runtime
            else _require_hash(runtime, f"{prefix}_RUNTIME_SHA256")
        ),
        "openclaw_config_sha256": (
            "" if allow_computed_config and not config_hash
            else _require_hash(config_hash, f"{prefix}_CONFIG_SHA256")
        ),
    }


def _with_openclaw_release(
    selection: DeploymentSelection,
    prefix: str,
    *,
    allow_computed_runtime: bool = False,
    allow_computed_config: bool = False,
    allow_selected_image_builds: bool = False,
) -> DeploymentSelection:
    if "openclaw" not in selection.components:
        return selection
    return DeploymentSelection(
        components=selection.components,
        apps_projects=selection.apps_projects,
        image_builds=selection.image_builds,
        **_release_fields(
            prefix,
            allow_computed_runtime=allow_computed_runtime,
            allow_computed_config=allow_computed_config,
            selected_image_builds=(
                selection.image_builds if allow_selected_image_builds else ()
            ),
        ),
    )


def _parse_components(value: str) -> tuple[str, ...]:
    if value == "":
        return ()
    pieces = tuple(part.strip() for part in value.split(","))
    if any(not part for part in pieces) or len(pieces) != len(set(pieces)):
        raise SelectionError("MANUAL_COMPONENTS must contain unique nonempty names")
    unknown = set(pieces).difference(COMPONENT_ORDER)
    if unknown:
        raise SelectionError(f"MANUAL_COMPONENTS contains unsupported names: {','.join(sorted(unknown))}")
    return _ordered(pieces, COMPONENT_ORDER)


def selection_for_event(repo_root: Path) -> tuple[DeploymentSelection, tuple[str, ...]]:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch":
        components = _parse_components(os.environ.get("MANUAL_COMPONENTS", ""))
        selection = DeploymentSelection(
            components,
            ("homelab",) if "apps" in components else (),
        )
        return _with_openclaw_release(selection, "MANUAL_OPENCLAW"), ()
    if event == "repository_dispatch":
        return _with_openclaw_release(
            DeploymentSelection(("openclaw",)),
            "OPENCLAW_PROMOTED",
            allow_computed_config=True,
        ), ()
    if event != "push":
        raise SelectionError("event must be push, workflow_dispatch, or repository_dispatch")
    before = os.environ.get("GITHUB_DEPLOYMENT_BASE_SHA", "").lower()
    current = os.environ.get("GITHUB_SHA", "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", before or ""):
        raise SelectionError("GITHUB_DEPLOYMENT_BASE_SHA must be exact lowercase 40/64-hex")
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", current or ""):
        raise SelectionError("GITHUB_SHA must be exact lowercase 40/64-hex")
    paths = _changed_paths(repo_root, before, current)
    return _with_openclaw_release(
        classify_paths(paths),
        "OPENCLAW_DEFAULT",
        allow_computed_runtime=True,
        allow_selected_image_builds=True,
    ), paths


def write_github_output(path: Path, selection: DeploymentSelection) -> None:
    values = {
        "components": ",".join(selection.components),
        "apps_projects": ",".join(selection.apps_projects),
        "openclaw_setup_commit": selection.openclaw_setup_commit,
        "openclaw_gateway_ref": selection.openclaw_gateway_ref,
        "openclaw_ctf_ref": selection.openclaw_ctf_ref,
        "openclaw_runtime_sha256": selection.openclaw_runtime_sha256,
        "openclaw_config_sha256": selection.openclaw_config_sha256,
        "openclaw_builds": ",".join(
            build for build in selection.image_builds if build.startswith("openclaw_")
        ),
        "t3_build": "true" if "t3code" in selection.image_builds else "false",
    }
    with Path(path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


APP_FAST_PREFIXES = (
    "apps/compose/homelab/",
    "apps/images/t3code/",
)
APP_MODEL_PREFIXES = ("apps/compose/homelab/",)
APP_MODEL_EXACT = frozenset(
    (
        "tests/docker/test_homelab_compose.py",
        "tests/docker/test_compose_secrets.py",
        "tests/docker/test_t3code_compose.py",
        "tests/docker/test_traefik_config.py",
    )
)
APP_FAST_EXACT = frozenset(
    (
        "scripts/ci/deploy-compose-via-ssh.sh",
        "scripts/ci/deploy_compose_release.py",
        "tests/docker/test_homelab_compose.py",
        "tests/docker/test_compose_secrets.py",
        "tests/repo/test_deploy_compose_release.py",
        "tests/repo/test_deploy_compose_via_ssh.py",
        "tests/repo/test_immutable_image_release.py",
    )
)
OPENCLAW_FAST_PREFIXES = ("infra/openclaw/",)
OPENCLAW_FAST_EXACT = frozenset(
    (
        "scripts/ci/deploy-openclaw-via-ssh.sh",
        "scripts/ci/openclaw_release.py",
        "scripts/ci/deploy_openclaw_release.py",
        "tests/repo/test_openclaw_release.py",
        "tests/repo/test_deploy_openclaw_release.py",
        "tests/repo/test_deploy_openclaw_via_ssh.py",
        "tests/infra/test_openclaw_native_role.py",
        "tests/infra/test_openclaw_codex_cwd_patch.py",
        "tests/infra/test_openclaw_discord_autothread_patch.py",
        "tests/infra/test_openclaw_skill_sync.py",
    )
)


def validation_scope(paths: Iterable[str]) -> str:
    normalized = tuple(path.replace("\\", "/") for path in paths if path)
    if not normalized:
        return "repo"

    def neutral(path: str) -> bool:
        return path.startswith("docs/") or path.endswith("/README.md")

    model_seen = any(
        path in APP_MODEL_EXACT or path.startswith(APP_MODEL_PREFIXES)
        for path in normalized
    )
    if model_seen and all(
        neutral(path) or path in APP_MODEL_EXACT or path.startswith(APP_MODEL_PREFIXES)
        for path in normalized
    ):
        return "apps-model"

    app_seen = any(
        path in APP_FAST_EXACT or path.startswith(APP_FAST_PREFIXES)
        for path in normalized
    )
    if app_seen and all(
        neutral(path) or path in APP_FAST_EXACT or path.startswith(APP_FAST_PREFIXES)
        for path in normalized
    ):
        return "apps"
    openclaw_seen = any(
        path in OPENCLAW_FAST_EXACT or path.startswith(OPENCLAW_FAST_PREFIXES)
        for path in normalized
    )
    if openclaw_seen and all(
        neutral(path)
        or path in OPENCLAW_FAST_EXACT
        or path.startswith(OPENCLAW_FAST_PREFIXES)
        for path in normalized
    ):
        return "openclaw"
    if all(neutral(path) or path.startswith("tests/") for path in normalized):
        return "repo"
    return "full"


def _changed_paths(repo_root: Path, before: str, current: str) -> tuple[str, ...]:
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", before):
        raise SelectionError("VALIDATION_BASE_SHA must be exact lowercase 40/64-hex")
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", current):
        raise SelectionError("GITHUB_SHA must be exact lowercase 40/64-hex")
    command = (
        ["git", "ls-tree", "-r", "--name-only", current]
        if before == NO_DEPLOYED_REVISION
        else ["git", "diff", "--no-renames", "--name-only", before, current, "--"]
    )
    result = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        action = "git ls-tree" if before == NO_DEPLOYED_REVISION else "git diff"
        raise SelectionError(f"{action} failed: {result.stderr.strip() or 'no diagnostics'}")
    return tuple(result.stdout.splitlines())


def write_validation_scope(path: Path, repo_root: Path) -> str:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "schedule":
        scope = "full"
    elif event == "repository_dispatch":
        scope = "openclaw"
    elif event == "workflow_dispatch":
        components = _parse_components(os.environ.get("MANUAL_COMPONENTS", ""))
        scope = (
            "apps" if components == ("apps",)
            else "openclaw" if components == ("openclaw",)
            else "full" if components
            else "repo"
        )
    elif event in ("push", "pull_request"):
        paths = _changed_paths(
            repo_root,
            os.environ.get("VALIDATION_BASE_SHA", "").lower(),
            os.environ.get("GITHUB_SHA", "").lower(),
        )
        scope = validation_scope(paths)
    else:
        raise SelectionError("unsupported validation event")
    with Path(path).open("a", encoding="utf-8") as output:
        output.write(f"validation_scope={scope}\n")
    return scope


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 2 and arguments[0] == "validation-scope":
        try:
            scope = write_validation_scope(
                Path(arguments[1]), Path(__file__).resolve().parents[2]
            )
        except (OSError, SelectionError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Validation scope: {scope}")
        return 0
    if len(arguments) != 1:
        print(
            "usage: select_deployment_components.py [validation-scope] GITHUB_OUTPUT",
            file=sys.stderr,
        )
        return 2
    try:
        selection, paths = selection_for_event(Path(__file__).resolve().parents[2])
        write_github_output(Path(arguments[0]), selection)
    except (OSError, SelectionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("Deployment components: " + (",".join(selection.components) or "none"))
    print("Application projects: " + (",".join(selection.apps_projects) or "none"))
    print("Image builds: " + (",".join(selection.image_builds) or "none"))
    for path in paths:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
