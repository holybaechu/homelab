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

## TickTick OAuth compatibility

TickTick's protected-resource metadata advertises `https://ticktick.com/`, while
its authorization-server document declares `https://ticktick.com`. Hermes's
bundled MCP SDK rejects this exact-string mismatch before browser authorization.

`patch_ticktick_oauth.py` adds one exception through Hermes's existing metadata
compatibility hook. It accepts only the official TickTick MCP endpoint, the exact
issuer pair above, and a direct GET response from
`https://ticktick.com/.well-known/oauth-authorization-server`. Redirected responses
and other issuer mismatches still go through the SDK's validation. The advertised
issuer, document issuer, callback checks, and token bindings are preserved.

An init container generates the patched provider in an `emptyDir` volume. Only
that file is mounted read-only over the application module; the image and PVC
are not patched. The generator refuses to run if its expected code hook changes,
so review it when updating the pinned image. Remove this workaround when the
provider metadata or upstream client handles this case correctly.

The `hermes-oauth` workflow runs the regression tests against the exact deployment
image, without network access or credentials. The tests exercise the real OAuth
flow through discovery and client registration, stopping before browser sign-in.

References: [TickTick MCP](https://help.ticktick.com/articles/7438129581631995904),
[Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp).
