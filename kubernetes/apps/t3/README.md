# T3 coding workspace

T3 owns coding conversations, real diffs, and direct user steering. Hermes keeps
planning, research, read-only investigation, and live operations. This is a
**staged installation, not an accepted coding-route cutover**. The Git-owned
`hermes/mcp-servers.yaml` entry stays disabled until the trial below passes.
No laptop software is required: open `https://t3.<DOMAIN>` from LAN or tailnet.

## Deployment and ownership

The apps layer installs the official [T3 v0.1.9 release](https://github.com/pingdotgg/t3code/releases/tag/v0.1.9),
not a maintainer test build. The init container checks the Linux x64 archive
against the publisher's `SHA256SUMS` and release-asset digest, then checks the
binary's version. Keep its entire extracted distribution together, including
client assets and native libraries. Upgrades change the version, checksum, and
version assertion together after reviewing the upstream release.

The existing digest-pinned Hermes image is reused only as a Linux tool image
with Git, Node, Python, curl, and CA certificates. Both container commands bypass
its gateway entrypoint. This avoids a new image build/registry pipeline; no
Hermes gateway, profile initialization, or Discord service starts here. T3 runs
as UID 10000 with a read-only root filesystem, no Kubernetes token, and no
Hermes volume, projected credentials, or model-token inheritance. T3's own
terminal and workers still have the container's network access and tool image;
this is not a network sandbox.

`t3-data` contains the private home `/var/lib/t3`, server state under
`/var/lib/t3/server`, and repositories under `/var/lib/t3/workspaces`. Pairing,
OAuth grants, native provider auth, chats, and diffs must stay private. The PVC
has Flux pruning disabled; deleting its namespace or the PVC manually remains
destructive. Back up the entire PVC with T3 stopped before upgrading; roll back
the binary only with a compatible database or a matching backup. Recreate
prevents concurrent servers writing its database. Never move/delete old
`hermes-data`, coder profiles, sessions, auth, or checkouts during this trial.

Ingress reuses `traefik/private-only` and `secure-headers`, plus HTTPS and T3's
native pairing/auth. It does not create an Authentik client or put forward-auth
in front of OAuth discovery. Confirm DNS, trusted TLS, LAN/tailnet access,
external denial, and Hermes-to-ingress reachability before pairing. Plain HTTP
Service DNS is not a supported OAuth endpoint. T3's `/mcp` must return 401 to
an unauthenticated client and advertise OAuth discovery with PKCE S256/DCR.
The UI shell loading is not proof that its protected APIs allow anonymous use.

## Runtime and first access

Use **native Codex**, installed through **Settings → Providers → Codex**, with
its own server-side sign-in. T3 manages the supported CLI distribution. Existing
Hermes `openai-codex` auth is not standalone Codex CLI auth; do not copy rotating
tokens, mount the assistant's secrets, or claim equivalent models/options before
provider discovery confirms them. Select the intended model and reasoning
options from the actual provider capability response. GitHub push/signing access
is not provisioned by this stage; keep the trial local and arrange separately
scoped credentials before expecting authenticated repository writes.

A Local ACP command (`hermes -p coder acp`) is supported by the two products,
but would require an installed/configured Hermes profile and an explicit auth,
permission, session-ownership, and process-lifetime contract on this new server.
It is not wired as a fallback. Native Codex is the smaller independent runtime;
its separate sign-in is an explicit operator gate, not silently borrowed auth.

After a separately approved rollout, an operator can mint an initial private
pairing link on the server:

```sh
kubectl -n homelab exec deployment/t3 -- /runtime/t3 auth pairing create \
  --base-dir /var/lib/t3/server --base-url https://t3.<DOMAIN> --ttl 5m
```

Keep that output out of chat/logs; the default initial administrator grant is
powerful. Open the link only on the intended browser. Use **Sharing & access**
to review/revoke clients. Outside-agent grants cover every project, not just a
UI-selected Git scope. Set both the trial project/thread and the outside-agent
grant to **Supervised** (`approval-required`), explicitly replacing the initial
Full access/Auto defaults. Approval mode is not filesystem confinement.

## Hermes MCP and acceptance

Use the [official outside-agent MCP flow](https://github.com/pingdotgg/t3code/blob/v0.1.9/docs/user/outside-agents.md)
and [Hermes native OAuth support](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp),
not a bridge or a second agent editor. Before cutover, use a disposable Hermes
profile with no native coder delegation for the isolated trial. In that profile:

```sh
hermes -p t3-trial mcp add t3code --url https://t3.<DOMAIN>/mcp \
  --auth oauth --connect-timeout 315
hermes -p t3-trial mcp login t3code
```

Create that blank profile with the normal Hermes profile workflow first. Approve
the sign-in with **Supervised**, using a fresh pairing code or an authorized
browser. The OAuth callback targets the client machine; follow Hermes's supported
headless callback instructions, rather than exposing another public port. Tokens
remain in the native Hermes OAuth store, outside Git. No service restart from the
conversation preparing this change is authorized.

Acceptance requires one harmless fixture repository and one authoritative thread:

1. Discover projects and `orchestrator_capabilities` through the released MCP.
   Confirm Codex is installed/authenticated and the requested model/options exist.
2. From Hermes, launch with explicit project, model, workspace strategy,
   `runtimeMode: "approval-required"`, and a stable `clientRequestId`. Record the
   returned thread ID/link and run ID. Repeat a lost request with the same ID,
   not another worker. Verify the same thread in the browser with real chat.
3. Have the worker make a harmless file change and execute its relevant check.
   Inspect the actual diff in T3. Send a direct human correction in that thread,
   confirm the same worker applies it, and read the correction/result via Hermes.
   Also verify Hermes `t3_thread_send` with explicit `steer` versus `queue` mode.
4. Exercise `t3_thread_wait` on the exact run through completion, including a
   bounded timeout and awaiting-user state. Do not infer completion from HTTP
   success, an idle thread, or a single wait response.
5. Interrupt an active harmless run with **Stop** and with the released
   `t3_thread_interrupt` tool with `stop: true` (there is no `t3_thread_stop` in v0.1.9). Read/wait
   back the exact terminal run state; inspect queued work separately. Verify no
   worker continues editing after the observed stop.
6. Start a separate thread directly in T3, then have Hermes read/steer that exact
   ID rather than duplicating it. Keep evidence IDs/check results, never tokens.

Only after those checks and operator approval, enable `t3code` in the Git-owned
MCP file. `configure_profiles.py` then removes only `advanced-delegation` from
the assistant's enabled plugins, retaining its route configuration and all old
state. Source instructions route **all** code changes through T3; an unavailable
T3 runtime becomes a blocker, not a native fallback. Authenticate the production
assistant's MCP connection with `hermes mcp login t3code`; trial-profile tokens
do not automatically become production grants.

The parent/operator owns merging, rollout timing, and active-work handling.
Bootstrap ConfigMap changes replace the Recreate Hermes Pod: finish or explicitly
stop active work first, preserve its IDs, then roll out from an independent
session. Use a new assistant chat after rollout to avoid a cached native tool
schema. Verify the real default-profile MCP connection, all-code routing,
thread link, and direct steering before calling cutover complete. Revoke/remove
the disposable trial profile only with explicit approval after preserving evidence.
For recovery, stop T3 edits before reverting the Git gate and intentionally
rolling Hermes back; never run both editors against one checkout.
