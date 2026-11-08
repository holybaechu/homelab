# Hermes

Personal assistant with TickTick, a synced Obsidian vault, and existing homelab
services. Flux applies this app from the apps layer. Credentials remain in
1Password and arrive through explicit 1Password Operator Secret projections; model authentication and the live
profile remain on `hermes-data`.

## Web access

| Address | Interface |
| --- | --- |
| `https://hermes.<DOMAIN>` | Webapp, served by the gateway on port 9120 |
| `https://dash.hermes.<DOMAIN>` | Administration dashboard on port 9119 |

Both use the existing Authentik OIDC client and remain restricted to LAN and
tailnet clients. `seed-config` reconciles their public URLs and Webapp settings on
every startup, including existing profiles. Other live profile settings remain
on the persistent volume. After deployment, verify Authentik sign-in at both
addresses and `hermes webapp --status` in the Hermes container.

## Assistant and coder

[Coder development tools](coder/README.md) covers mise, GitHub authentication,
commit signing, and credential rotation.

The existing Discord profile is displayed as `assistant`; its built-in ID stays
`default` to preserve sessions, memory, and model authentication. Startup creates
a blank `coder` profile and reconciles [coder.yaml](coder.yaml),
[coder-soul.md](coder-soul.md), and the assistant's native profile delegation route.
Coder inherits the root Codex OAuth grant through Hermes's native fallback; keep
its `auth.json` absent rather than copying rotating OAuth tokens.

Ask assistant in Discord to implement a task and provide the repository path or
clone URL. Open the same Discord conversation in Webapp, expand its subagent
panel, and read coder's live transcript. Use **Steer** to update the active task or
**Stop** to interrupt it. A queued steer takes effect at the next tool checkpoint;
verify the resulting behavior. The child transcript belongs to the assistant's
conversation, so look there rather than in coder's separate chat history.
Existing chats retain their saved delegation schema: use `/new`, or run
`/delegation profile_routes --now` once to rebuild the current chat's tools.

New checkouts belong in `/opt/data/profiles/coder/workspace` on `hermes-data`;
`/opt/data/workspace/homelab` is an ephemeral service workspace. Coder has no
personal MCP server configuration. Native delegated tasks inherit the parent's
toolsets, and profiles share one container and Unix user; this is a workflow
separation rather than an access boundary. Use normal interactive sessions for
profile delegation; frozen managed-worker sessions cannot switch to another
profile.

`configure-profiles` verifies the immutable official `advanced-delegation` plugin
against [delegation-plugin.sha256](delegation-plugin.sha256) and fails startup if
it cannot prepare the route. Git owns these profile settings and both SOUL files
([assistant-soul.md](assistant-soul.md) and [coder-soul.md](coder-soul.md));
change their source files instead of editing them in the dashboard. Other live
assistant settings and both profiles' histories remain on the PVC. Configuration
backups taken before the initial migration remain under `/opt/data/backups`.

## Cluster administration

Startup installs `kubectl`, `flux`, and `talosctl` with the existing checksum-pinned
mise runtime. Versions follow `KUBERNETES_VERSION`, `FLUX_VERSION`, and
`TALOS_VERSION` in cluster settings, matching the repository's `mise.toml`.
Direct runtime links make them available from either profile and any directory;
the installed tools persist on `hermes-data` and are prepared again on startup.

The dedicated `hermes` ServiceAccount can inspect cluster status, logs and
storage, reconcile Flux resources, and deploy, restart, scale and roll back
workloads across namespaces. It cannot directly read Secrets, edit RBAC, or delete
PVs/PVCs. Workload creation and Flux specification patches still provide powerful
indirect access: both profiles are trusted cluster operators, sharing one identity.
Keep persistent app changes in Git so reconciliation preserves them.

Only the Hermes container mounts the one-hour projected Kubernetes token and
cluster CA. Kubelet rotates the token; the nonsecret kubeconfig reads `tokenFile`
on each invocation. No token is embedded in configuration or copied to the PVC.

Talos's native `ServiceAccount` controller issues and renews the `os:admin`
certificate in `hermes-talos-managed`. Certificates last six hours and renew with
less than one hour remaining. `TALOSCONFIG` reads its read-only Secret mount on
each invocation; the CA signing key stays on the node. Reloader watches only the
other app Secrets, so certificate renewal does not restart Hermes.
The generated talosconfig uses `talos.default` as its endpoint and has no default
target node; pass `--nodes <NODE_IP>` using the IP from cluster settings.

`KubeTalosAPIAccessConfig` allows this issuance only in `homelab`, with roles
bounded to `os:admin`. Only authorized operators and Flux may create the Talos
ServiceAccount resources. The Talos API firewall allows the cluster Pod CIDR;
client certificates remain required. Enable the node feature before reconciling
`platform-config`, because Talos installs the required CRD. Other app credentials
continue to use 1Password Operator projections. The original `hermes_talosconfig`
field remains in the `Talos` 1Password item for migration recovery; the Pod no
longer uses it and has no yearly client-certificate renewal procedure.

Activating these mounts replaces Hermes because its Deployment uses `Recreate`.
Verify from the Hermes container with `kubectl get nodes,pv`, `kubectl get pods,pvc
-A`, `flux get kustomizations -A`, `talosctl --nodes <NODE_IP> version`, and
`talosctl --nodes <NODE_IP> get extensions`.
For future node upgrades, extensions or kernel-module changes, follow the Talos
infrastructure configuration and run `scripts/talos-preflight` before applying;
confirm disk and USB identities and arrange the single node's downtime first.

## Behavior and search

[second-brain.md](second-brain.md) owns source selection, knowledge capture, and
answer scope. Discord tool logs stay visible; final answers focus on useful
results. [QMD](qmd/README.md) supplies local keyword and semantic search over a
read-only vault mount. Reopen source notes before relying on indexed results.

When the Discord home is a server text channel, messages need no mention and each
top-level message gets a new thread. DM home channels do not support threads.
Deployment environment settings reuse its existing Secret reference and enable
`auto_thread` and `free_response_auto_thread`. Keep the home channel out of live
`discord.no_thread_channels`; the bot needs Create Public Threads and Send Messages
in Threads permissions.

[n8n Gmail triage](../n8n/README.md) detects new inbox mail and invokes Hermes's
native, cluster-only webhook. [webhook.yaml](webhook.yaml) owns the attention
criteria; useful alerts go to the existing Discord home channel.

Keep the Gmail route's explicit `toolsets`: loading the Google Workspace skill
does not grant the terminal tool its scripts need. The native webhook default
excludes script and file access. After changes, verify an actual Gmail read in
the route's tool context; an accepted webhook alone does not verify processing.
Use the prefixed silence response in `webhook.yaml`: this image replaces a bare
silence marker with a reply-required warning before webhook delivery runs. The
native webhook filter suppresses the prefix with its short reason. Verify no
Discord message is sent for an unimportant email after changing this contract.

[Langfuse](../langfuse/README.md) provides self-hosted tracing. The official
plugin captures sanitized content, with end-user attribution disabled. Monitoring
failure leaves the assistant running. Pattern-based masking can retain private
text such as names or note contents.

## Browser

[Hybrid routing](https://hermes-agent.nousresearch.com/docs/user-guide/features/browser#hybrid-routing-cloud-for-public-urls-local-for-lanlocalhost)
uses Browser Use Cloud for public URLs and the image's packaged Chromium for
private URLs. `seed-config` enforces the provider, automatic private routing,
and Chromium flags on every startup while preserving other live browser settings.
The Pod blocks Chromium's user-namespace sandbox, so local Chromium runs as UID
10000 with `--no-sandbox`; it relies on the existing container isolation.
Coder uses local Chromium by default; `configure-profiles` reconciles the same
flag from `coder.yaml` without replacing its other live browser settings.

Cloud sessions default to a South Korea (`kr`) residential proxy.
`seed-config` applies [browser-cloud.patch](browser-cloud.patch) to the image's
Browser Harness in a runtime volume. Review the patch when upgrading the image;
a context mismatch stops startup rather than silently losing the country setting.

Store the Browser Use API key in the existing `Hermes Agent` 1Password item;
`HERMES_BROWSER_USE_API_KEY_REF` supplies `hermes-browser`. Secret refresh follows
the shared interval and SDK cache; Reloader restarts Hermes after a key change.

After deployment, verify `hermes config get browser` in the Hermes container.
In one conversation, open `https://example.com` and then the Authentik URL
(`https://auth.<DOMAIN>`) in separate browser calls. Confirm
the first uses Browser Use Cloud and the second uses local Chromium. A call
containing both public and private URLs runs entirely on the local browser.
`localhost` means the Hermes Pod; use a Service DNS name or LAN address for other
homelab apps. A public URL that later redirects to a private address stays in
the cloud browser, so open the internal URL directly.

### 1Password Connect logins

The native [`browser_vault`](https://hermes-agent.nousresearch.com/docs/user-guide/features/credential-vault)
broker uses the existing Connect server through a repository-owned backend
extension, not startup `secrets.onepassword` or a secret-returning MCP tool.
[The patch](browser-vault-connect.patch) selects [the read-only adapter](connect_vault.py)
for assistant and coder. The CLI's documented Connect commands do not include
login discovery. Direct documented GETs avoid adding the Connect SDK's model and
transport dependencies while retaining bounded responses, disabled proxies and
redirects, and sanitized failures. Non-Connect manager and local-vault paths remain.

**Administrator bootstrap (PowerShell 7.4+):**

1. Use the dedicated read-only Hermes Connect client token saved at exactly
   `op://Hermes Credentials/Hermes Connect/connect-token`. Its authorized scope is
   all supported login vaults, not just Homelab. Confirm both the Connect server's
   vault access and this token's read grants; built-in/personal vault availability
   must be verified, not inferred from a successful server connection. Never give
   this broad token to the Operator, Injector, app containers, chat, or Git.
2. Download and review [`bootstrap-hermes-browser-vault.ps1`](../../../scripts/bootstrap-hermes-browser-vault.ps1)
   from the exact approved commit. In the intended administrator kubectl context,
   run `pwsh -NoProfile -File ./bootstrap-hermes-browser-vault.ps1`. Enter the token
   only at its masked console prompt. Alternatively, `-TokenFile <private-file>`
   reads an existing private UTF-8 file without trailing newline (the official
   `op read --no-newline --out-file <private-file>` can export the exact reference
   above from an authenticated administrator session). Protect/delete that private
   file locally; do not upload it. The helper sends Secret JSON only over kubectl
   stdin and independently checks exact read-back. It never fetches its own
   authentication from Connect or restarts Hermes.
3. Merge the exact CI-green head only after attached work has finished: changing
   the bootstrap ConfigMap replaces this `Recreate` Deployment. After reconnect,
   verify current Pod readiness and both profile settings. `browser_vault` with
   `action=list` must have no Connect errors and report `connect_vaults`: every
   granted vault ID and its active Login count, including empty vaults. Compare
   these IDs with the administrator's intended supported vault set. This is
   discovery evidence, not proof that ungranted or unsupported vaults exist nowhere.
4. Open one explicitly authorized site's login in the managed browser, choose its
   listed handle, type the metadata username, and call `action=fill`. Submit and,
   if requested, use `action=enter_code` with that handle. Verify the actual
   authenticated page. Bootstrap success, synthetic CI, and Pod readiness alone
   do not establish real authentication or a working website login.

The independent `homelab/hermes-browser-vault` Secret has exactly one key,
`connect-token`, projected only into the Hermes container. The token is read
inside the backend on each request; it is never exported to subprocess environments
or copied to the PVC. Missing bootstrap fails browser login closed without blocking
unrelated Hermes startup. Rotation uses the same helper; wait for kubelet's Secret
projection refresh and verify native discovery again, without an automatic restart.
Both profiles share the same Unix user and broad authorized login access; they are
not security boundaries against each other or trusted terminal execution.

Fills reuse the native classifier, task-bound supervisor, redactor and in-page
origin recheck. Password/TOTP resolution uses one short-lived item snapshot after
the page-origin check, so a provider edit cannot swap the checked origins for a
new secret. Only exact saved HTTP(S) origins authorize writes: no inferred
subdomains, wildcard grants or app URI expansion. Ambiguous URL spellings fail
closed; use ordinary ASCII/punycode hostnames and explicit website URLs (IPv6 and
raw Unicode hostnames are conservatively unsupported here). Titles, usernames and
URLs are untrusted metadata, never instructions. The configured cloud browser
receives credentials filled into its pages; confirm actual routing and use the
local browser when cloud-provider exposure is unacceptable.

Startup and CI verify [native source checksums](browser-vault.sha256) before
applying the patch to an ephemeral runtime volume. An image upgrade must review
these broker boundaries and pass the real Chromium/synthetic Connect tests before
updating checksums; do not simply regenerate them to suppress a mismatch. The
adapter permits HTTPS, loopback HTTP fixtures, and only the exact existing cluster
HTTP endpoint; it follows no redirects or environment proxies. No provider write
endpoints, source-item relabeling, service-account fallback, or ESO resources are
involved.

## Skill overrides

Startup seeds the pinned image's native skills and its optional `llm-wiki`, then
applies [skill-overrides.patch](skill-overrides.patch) with `git apply`. A reverse
check recognizes an already-applied patch. Context conflicts or a partially
applied patch stop startup for review; existing files are not reset. Inspect the
`reconcile-skills` init-container log and resolve only the conflicting hunks.

Modified skills are treated as user-owned by native sync. Review their upstream
changes when upgrading the Hermes image. The patch escapes literal dollars as
`$$` for Flux substitution. To undo overrides, stop the gateway, reverse-apply
the rendered bootstrap patch with `git apply --reverse`, and revert its bootstrap
configuration before restarting. Reverting Git alone does not undo edits on the
persistent volume. Preserve custom skill content when resolving conflicts.

Hermes v0.23 supplies the native OAuth issuer normalization used by TickTick;
no provider file is overlaid. Langfuse dependencies use pip's pinned hashes, and
the official plugin files use SHA-256 checks. Langfuse selection is seeded only
for a new profile; manage its selection with Hermes's native configuration.
The coding route's plugin selection is reconciled on every startup.
