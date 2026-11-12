"""Reconcile the Git-owned profile settings without cloning assistant state."""

from pathlib import Path

import hermes_yaml as yaml

from hermes_cli.profiles import write_profile_meta
from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
from utils import atomic_yaml_write


PROFILE_SYNC = "/opt/coder/bin/hermes-profile-sync"


def configure_profile_sync(config: dict) -> None:
    for event, action in (("on_session_start", "start"), ("on_session_end", "end")):
        hooks = config.setdefault("hooks", {}).setdefault(event, [])
        command = f"{PROFILE_SYNC} {action}"
        hooks[:] = [entry for entry in hooks if entry.get("command") not in (PROFILE_SYNC, command)]
        hooks.append({"command": command, "timeout": 60})


def configure_browser_vault(config: dict) -> None:
    config.setdefault("vault", {}).setdefault("onepassword", {}).update({
        "enabled": True,
        "connect_host": "http://onepassword-connect.onepassword.svc.cluster.local:8080",
        "connect_token_file": "/var/run/hermes-browser-vault/connect-token",
    })


def remove_coder_delegation(config: dict) -> None:
    enabled = config.setdefault("plugins", {}).setdefault("enabled", [])
    enabled[:] = [name for name in enabled if name != "advanced-delegation"]
    config["plugins"].get("entries", {}).pop("advanced-delegation", None)
    delegation = config.get("delegation", {})
    if delegation.get("backend") == "profile_routes":
        delegation.pop("backend")
        delegation.pop("inherit_mcp_toolsets", None)
    backends = delegation.get("backends", {})
    backends.pop("profile_routes", None)
    if not backends:
        delegation.pop("backends", None)
    if not delegation:
        config.pop("delegation", None)


def reconcile(root: Path, bootstrap: Path) -> None:
    config = yaml.safe_load((root / "config.yaml").read_text()) or {}
    assistant = yaml.safe_load((bootstrap / "config.yaml").read_text())
    for section in ("model", "agent"):
        for key, value in assistant[section].items():
            config.setdefault(section, {}).setdefault(key, value)
    configure_browser_vault(config)
    servers = yaml.safe_load((bootstrap / "mcp-servers.yaml").read_text())
    config.setdefault("mcp_servers", {}).update(servers)
    remove_coder_delegation(config)
    configure_profile_sync(config)
    atomic_yaml_write(root / "config.yaml", config, sort_keys=False)
    # The built-in default ID owns existing Discord sessions and OAuth grants.
    write_profile_meta(root, display_name="assistant")
    from agent.shell_hooks import _record_approval

    token = set_hermes_home_override(root)
    try:
        for event, action in (("on_session_start", "start"), ("on_session_end", "end")):
            _record_approval(event, f"{PROFILE_SYNC} {action}")
    finally:
        reset_hermes_home_override(token)
    print("Reconciled assistant profile.")


if __name__ == "__main__":
    reconcile(get_hermes_home(), Path("/bootstrap"))
