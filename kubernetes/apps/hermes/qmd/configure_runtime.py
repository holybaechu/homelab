"""Set the homelab MCP default in the locked QMD package during bootstrap."""

from pathlib import Path


UPSTREAM = 'rerank: z.boolean().optional().default(true).describe("Rerank results using LLM (default: true). Set to false for faster results on CPU-only machines.")'
HOMELAB = 'rerank: z.boolean().optional().default(false).describe("Rerank results using LLM (default: false). Enable only when lightweight searches leave a meaningful recall gap.")'


def configure(path: Path) -> None:
    source = path.read_bytes().decode("utf-8")
    if source.count(HOMELAB) == 1 and UPSTREAM not in source:
        return
    if source.count(UPSTREAM) != 1 or HOMELAB in source:
        raise RuntimeError("QMD MCP schema changed; review the pinned runtime before upgrading")
    path.write_bytes(source.replace(UPSTREAM, HOMELAB).encode("utf-8"))


if __name__ == "__main__":
    configure(Path("/opt/qmd/node_modules/@tobilu/qmd/dist/mcp/server.js"))
