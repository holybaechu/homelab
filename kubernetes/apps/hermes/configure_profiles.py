"""Reconcile the Git-owned profile settings without cloning assistant state."""

from pathlib import Path

import hermes_yaml as yaml

from hermes_cli.profiles import create_profile, profile_exists, write_profile_meta
from hermes_constants import get_hermes_home
from utils import atomic_yaml_write


def reconcile(root: Path, bootstrap: Path) -> None:
    (root / "SOUL.md").write_text((bootstrap / "assistant-soul.md").read_text())
    if not profile_exists("coder"):
        create_profile("coder", no_alias=True)

    coder = root / "profiles" / "coder"
    (coder / "workspace").mkdir(exist_ok=True)
    desired = yaml.safe_load((bootstrap / "coder.yaml").read_text())
    config = yaml.safe_load((coder / "config.yaml").read_text()) or {}
    config.update(desired)
    atomic_yaml_write(coder / "config.yaml", config, sort_keys=False)
    (coder / "SOUL.md").write_text((bootstrap / "coder-soul.md").read_text())
    write_profile_meta(
        coder,
        display_name="coder",
        description="Implement and verify coding tasks in a persistent workspace.",
        description_auto=False,
    )

    config = yaml.safe_load((root / "config.yaml").read_text()) or {}
    plugins = config.setdefault("plugins", {})
    enabled = plugins.setdefault("enabled", [])
    if "advanced-delegation" not in enabled:
        enabled.append("advanced-delegation")
    # Git owns this route; unrelated delegation tuning stays live on the PVC.
    delegation = config.setdefault("delegation", {})
    delegation["backend"] = "profile_routes"
    delegation["inherit_mcp_toolsets"] = False
    delegation.setdefault("backends", {})["profile_routes"] = {
        "profiles": [
            {"name": "coder", "description": "Implement, debug, and verify code changes."}
        ]
    }
    atomic_yaml_write(root / "config.yaml", config, sort_keys=False)
    # The built-in default ID owns existing Discord sessions and OAuth grants.
    write_profile_meta(root, display_name="assistant")
    print("Reconciled assistant and coder profiles.")


if __name__ == "__main__":
    reconcile(get_hermes_home(), Path("/bootstrap"))
