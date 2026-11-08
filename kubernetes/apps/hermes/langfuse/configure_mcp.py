"""Prepare the native MCP client's Basic header from the existing project keys."""

import base64
import os

from hermes_cli.config import remove_env_value, require_env_writable, save_env_value


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


if __name__ == "__main__":
    configure()
