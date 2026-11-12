"""Reconcile the Git-owned profile settings without cloning assistant state."""

from pathlib import Path

import hermes_yaml as yaml

from hermes_cli.profiles import create_profile, profile_exists, write_profile_meta
from hermes_constants import get_hermes_home
from utils import atomic_yaml_write


def configure_browser_vault(config: dict) -> None:
    config.setdefault("vault", {}).setdefault("onepassword", {}).update({
        "enabled": True,
        "connect_host": "http://onepassword-connect.onepassword.svc.cluster.local:8080",
        "connect_token_file": "/var/run/hermes-browser-vault/connect-token",
    })


def configure_coding_route(config: dict, *, t3_enabled: bool) -> None:
    enabled = config.setdefault("plugins", {}).setdefault("enabled", [])
    if t3_enabled:
        enabled[:] = [name for name in enabled if name != "advanced-delegation"]
        # Retain historical route settings for recovery; the plugin is inactive.
        return
    if "advanced-delegation" not in enabled:
        enabled.append("advanced-delegation")
    delegation = config.setdefault("delegation", {})
    delegation["backend"] = "profile_routes"
    delegation["inherit_mcp_toolsets"] = False
    delegation.setdefault("backends", {})["profile_routes"] = {
        "profiles": [
            {"name": "coder", "description": "Implement, debug, and verify code changes."}
        ]
    }


def reconcile(root: Path, bootstrap: Path) -> None:
    history_exists = (root / ".instruction-history.git").exists()
    if not (root / "SOUL.md").exists() and not history_exists:
        (root / "SOUL.md").write_text((bootstrap / "assistant-soul.md").read_text())
    new_coder = not profile_exists("coder")
    if new_coder:
        create_profile("coder", no_alias=True)

    coder = root / "profiles" / "coder"
    (coder / "workspace").mkdir(exist_ok=True)
    desired = yaml.safe_load((bootstrap / "coder.yaml").read_text())
    coder_config = coder / "config.yaml"
    config = yaml.safe_load(coder_config.read_text()) if coder_config.exists() else {}
    config = config or {}
    config.setdefault("browser", {}).update(desired.pop("browser"))
    if new_coder:
        config.update(desired)
    else:
        for key, value in desired.items():
            config.setdefault(key, value)
    configure_browser_vault(config)
    atomic_yaml_write(coder / "config.yaml", config, sort_keys=False)
    if new_coder or (not (coder / "SOUL.md").exists() and not history_exists):
        (coder / "SOUL.md").write_text((bootstrap / "coder-soul.md").read_text())
    write_profile_meta(
        coder,
        display_name="coder",
        description="Implement and verify coding tasks in a persistent workspace.",
        description_auto=False,
    )

    config = yaml.safe_load((root / "config.yaml").read_text()) or {}
    assistant = yaml.safe_load((bootstrap / "config.yaml").read_text())
    for section in ("model", "agent"):
        for key, value in assistant[section].items():
            config.setdefault(section, {}).setdefault(key, value)
    configure_browser_vault(config)
    servers = yaml.safe_load((bootstrap / "mcp-servers.yaml").read_text())
    config.setdefault("mcp_servers", {}).update(servers)
    configure_coding_route(config, t3_enabled=servers["t3code"]["enabled"] is True)
    atomic_yaml_write(root / "config.yaml", config, sort_keys=False)
    # The built-in default ID owns existing Discord sessions and OAuth grants.
    write_profile_meta(root, display_name="assistant")
    from instruction_history import configure_hooks

    configure_hooks(root)
    print("Reconciled assistant and coder profiles.")


if __name__ == "__main__":
    reconcile(get_hermes_home(), Path("/bootstrap"))
