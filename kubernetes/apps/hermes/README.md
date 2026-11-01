# Hermes MCP connections

Edit `mcp-servers.yaml` to manage MCP connections through GitOps. The generated
ConfigMap's hash changes the Deployment when these definitions change, so Flux
starts a new pod. Its init container replaces the live `mcp_servers` section using
Hermes's CLI, preserving other settings in `/opt/data/config.yaml`.

This file owns the complete MCP server list. Dashboard changes to that list are
replaced on the next pod start. Remove a connection from the file to remove it
from the live configuration. OAuth token files are kept separately on the PVC;
removing a definition does not revoke its authorization or delete those files.

## TickTick authorization

The official TickTick endpoint uses OAuth. After Flux deploys the configuration:

1. Open the Hermes dashboard and sign in.
2. Open **MCP**, find `ticktick`, and select **Authorize**.
3. Complete the TickTick sign-in and authorization in your browser.
4. Use **Test** to check the connection, then run `/reload-mcp` in the Hermes chat
   that will use TickTick and ask it to list your TickTick projects.

Hermes saves and refreshes the OAuth tokens under `/opt/data/mcp-tokens/`. These
credentials survive pod replacement and must not be added to Git or a ConfigMap.

References: [TickTick MCP](https://help.ticktick.com/articles/7438129581631995904),
[Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp).
