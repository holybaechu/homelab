"""Generate the pinned Hermes provider with one TickTick metadata exception.

TickTick advertises https://ticktick.com/ but its discovery document names
https://ticktick.com. Reuse Hermes's existing metadata compatibility hook for
this exact pair, leaving SDK issuer validation unchanged for other responses.
"""

from pathlib import Path
import sys


def patch_provider(source: str) -> str:
    original = "        if not metadata_issued_by_origin(metadata, self.context.auth_server_url, response):\n"
    replacement = '''        # Homelab workaround for TickTick's inconsistent public issuer metadata.
        # Only the official MCP endpoint and its direct HTTPS discovery response qualify.
        ticktick_root_issuer = (
            str(self.context.server_url) in ("https://mcp.ticktick.com", "https://mcp.ticktick.com/")
            and self.context.auth_server_url == "https://ticktick.com/"
            and str(metadata.issuer) == "https://ticktick.com"
            and str(response.url) == "https://ticktick.com/.well-known/oauth-authorization-server"
            and response.request.method == "GET"
            and response.status_code == 200
            # HTTPX history also includes prior OAuth 401/200 responses.
            and not any(300 <= previous.status_code < 400 for previous in response.history)
        )
        if not (
            metadata_issued_by_origin(metadata, self.context.auth_server_url, response)
            or ticktick_root_issuer
        ):
'''
    if source.count(original) != 1:
        raise RuntimeError("Hermes OAuth provider changed; review the TickTick patch before upgrading")
    patched = source.replace(original, replacement)
    compile(patched, "mcp_oauth_provider.py", "exec")
    return patched


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_ticktick_oauth.py OUTPUT_FILE")
    source = Path("/opt/hermes/tools/mcp_oauth_provider.py").read_text()
    output = Path(sys.argv[1])
    output.write_text(patch_provider(source))
    output.chmod(0o444)
