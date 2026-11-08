"""Prepare the native MCP client's Basic header from the existing project keys."""

import base64
import os

from hermes_cli.config import remove_env_value, require_env_writable, save_env_value
from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override


def configure() -> None:
    name = "HERMES_LANGFUSE_MCP_AUTH"
    require_env_writable(name, "set")
    public = os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("HERMES_LANGFUSE_SECRET_KEY", "")
    if public and secret:
        header = base64.b64encode(f"{public}:{secret}".encode()).decode()
        save_env_value(name, f"Basic {header}")
    else:
        # Do not keep authenticating with stale credentials after key removal.
        remove_env_value(name)

    # Routed profiles deliberately do not inherit the assistant's process secrets.
    token = set_hermes_home_override(get_hermes_home() / "profiles" / "coder")
    try:
        for suffix, value in (
            ("PUBLIC_KEY", public), ("SECRET_KEY", secret),
            ("BASE_URL", "http://langfuse.homelab.svc.cluster.local:3000"),
        ):
            key = f"HERMES_LANGFUSE_{suffix}"
            require_env_writable(key, "set" if value else "remove")
            if public and secret:
                save_env_value(key, value)
            else:
                remove_env_value(key)
    finally:
        reset_hermes_home_override(token)


if __name__ == "__main__":
    configure()
