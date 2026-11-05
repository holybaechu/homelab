"""Hermes' local MCP interface for OpenCloud, Zipline and qBittorrent."""

from contextlib import asynccontextmanager
import site
from typing import Annotated, Any, Literal

site.addsitedir("/opt/homelab/python")

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from services import Services, remove_working_file


@asynccontextmanager
async def lifespan(server):
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as http:
        yield Services(http)


server = MCPServer("homelab_mcp", lifespan=lifespan, log_level="ERROR")
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
PUBLIC = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
Limit = Annotated[int, Field(ge=1, le=100, description="Maximum results per call")]
Offset = Annotated[int, Field(ge=0, description="Use next_offset from the previous result")]
Space = Annotated[str, Field(description="Exact OpenCloud space name or ID; 'personal' selects the personal space")]
Path = Annotated[str, Field(description="Path relative to that space, for example Reports/budget.pdf")]
LocalFile = Annotated[str, Field(description="Absolute local file path inside the Hermes workspace or Obsidian vault")]


async def invoke(ctx: Context, operation: str, **kwargs) -> dict[str, Any]:
    try:
        return await getattr(ctx.request_context.lifespan_context, operation)(**kwargs)
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        guidance = {401: "Refresh the service credential in 1Password",
                    403: "Check this account's permission for the requested operation",
                    404: "List the space or items again and choose an existing path or ID",
                    409: "Choose an unused destination name",
                    412: "The destination already exists; choose a new filename",
                    413: "The file exceeds the service upload limit",
                    429: "Wait before retrying"}.get(status, "Check service health and the supplied inputs")
        raise ToolError(f"Service returned HTTP {status}. {guidance}.") from None
    except httpx.RequestError:
        raise ToolError("Service connection failed or timed out. Check service health before retrying.") from None
    except (ValueError, FileNotFoundError) as error:
        raise ToolError(str(error)) from None
    except Exception:
        # Service/library exceptions may include request details; never expose credentials.
        raise ToolError("Service operation failed. Verify the selected item, account access and service health.") from None


@server.tool(annotations=READ)
async def cloud_list_spaces(ctx: Context) -> dict[str, Any]:
    """List OpenCloud spaces and their IDs before browsing, searching or selecting a save destination."""
    return await invoke(ctx, "cloud_list_spaces")


@server.tool(annotations=READ)
async def cloud_list_files(ctx: Context, space: Space = "personal", path: Path = "",
                           limit: Limit = 30, offset: Offset = 0) -> dict[str, Any]:
    """List one folder's files, directories, sizes and timestamps. Follow next_offset when present."""
    return await invoke(ctx, "cloud_list_files", space=space, path=path, limit=limit, offset=offset)


@server.tool(annotations=READ)
async def cloud_search_files(ctx: Context, query: Annotated[str, Field(min_length=1, max_length=500,
                             description="OpenCloud search text, for example budget or name:*.pdf")],
                             space: Space = "personal", limit: Limit = 30) -> dict[str, Any]:
    """Search OpenCloud's index within a selected space without downloading its contents."""
    return await invoke(ctx, "cloud_search_files", query=query, space=space, limit=limit)


@server.tool(annotations=WRITE)
async def cloud_download_file(ctx: Context, path: Path, space: Space = "personal") -> dict[str, Any]:
    """Download one selected OpenCloud file to temporary workspace storage for reading or processing."""
    return await invoke(ctx, "cloud_download_file", space=space, path=path)


@server.tool(annotations=WRITE)
async def cloud_upload_file(ctx: Context, local_path: LocalFile, path: Path, space: Space = "personal") -> dict[str, Any]:
    """Save a local file into an existing OpenCloud folder. Existing files are never overwritten."""
    return await invoke(ctx, "cloud_upload_file", local_path=local_path, space=space, path=path)


@server.tool(annotations=PUBLIC)
async def zipline_upload_file(ctx: Context, local_path: LocalFile) -> dict[str, Any]:
    """Publish a selected local file and return its i.hchu.me URL. Use when the user requests sharing."""
    return await invoke(ctx, "zipline_upload_file", local_path=local_path)


@server.tool(annotations=PUBLIC)
async def zipline_shorten_url(ctx: Context, url: str, slug: str | None = None) -> dict[str, Any]:
    """Create an l.hchu.me short link for an HTTP(S) destination, optionally with a custom slug."""
    return await invoke(ctx, "zipline_shorten_url", url=url, slug=slug)


@server.tool(annotations=READ)
async def qbt_get_status(ctx: Context) -> dict[str, Any]:
    """Get qBittorrent's version, connectivity and current transfer speeds."""
    return await invoke(ctx, "qbt", action="status")


@server.tool(annotations=READ)
async def qbt_list_torrents(ctx: Context, status_filter: Literal[
        "all", "downloading", "completed", "active", "inactive", "stalled", "errored"] = "all",
        limit: Limit = 30, offset: Offset = 0) -> dict[str, Any]:
    """List torrent hashes, progress, state and content_path. Completed /public or /downloads files belong to the corresponding OpenCloud space."""
    return await invoke(ctx, "qbt", action="list", status_filter=status_filter, limit=limit, offset=offset)


@server.tool(annotations=PUBLIC)
async def qbt_add_torrent(ctx: Context, url: str) -> dict[str, Any]:
    """Start a download from a magnet link or torrent URL. Check qbt_list_torrents for completion."""
    return await invoke(ctx, "qbt", action="add", url=url)


@server.tool(annotations=WRITE)
async def qbt_set_running(ctx: Context, torrent_hash: str, running: bool) -> dict[str, Any]:
    """Start or stop a single torrent chosen by its hash. Retains all downloaded files."""
    return await invoke(ctx, "qbt", action="set_running", torrent_hash=torrent_hash, running=running)


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                        idempotent_hint=False, open_world_hint=False))
async def homelab_remove_working_file(local_path: str) -> dict[str, Any]:
    """Remove a temporary file returned by a homelab download tool after processing or delivery."""
    try:
        return remove_working_file(local_path)
    except (ValueError, OSError) as error:
        raise ToolError(str(error)) from None


if __name__ == "__main__":
    server.run(transport="stdio")
