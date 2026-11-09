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
Startup also reconciles assistant's `model` selection and the `agent` inference
settings in [config.yaml](config.yaml): `gpt-6.1-sol` through `openai-codex`,
`xhigh` reasoning, and Fast (`service_tier: priority`). Coder's inference settings
remain separate. Existing chats can retain session-specific overrides; start a
new chat to use the profile defaults.
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

## Laptop control with Cua

The official [Cua CLI](https://github.com/trycua/cua/releases/tag/cua-sdk-v0.16.0)
0.16.0 is installed as `/opt/coder/bin/cua` by `install-coder-tools`, using the
SHA256 in `coder/tools.sha256`. Only the CLI is extracted; Hermes runs its standard
stdio `cua mcp --embedded`, not a host service or a cloud relay. MCP and the
registration examples set `CUA_TELEMETRY=0` to disable CLI telemetry. `seed-config`
reconciles [mcp-servers.yaml](mcp-servers.yaml) through `hermes config set` on pod
startup. An operator-reviewed deployment must complete both init steps before a
new assistant session can discover Cua. Do not edit live profile YAML or restart
Hermes from the session being used to prepare this change.

The MCP permission list and Hermes allowlist expose only `list_spaces`,
`space_bash`, `space_job`, `space_write`, `upload`, `download`, `list_tools`, and
`call_tool`. No provisioning, deletion, cloud account, or agent-key management
tools are exposed. `call_tool` invokes services advertised by a registered Space,
including native desktop tools; it is not a per-desktop-tool security boundary.
Shell, file and UI access control the signed-in laptop user's real desktop and
files, not an isolated sandbox. Registration is an operator action, outside MCP.
Native delegated coder tasks inherit these parent tools; no coder MCP config is
needed.

### Onboard the existing Headscale laptops

1. Both laptops are already enrolled: Mac `100.64.0.3`, Windows `100.64.0.4`.
   Install the official [Cua Spaces app](https://github.com/trycua/cua/releases/tag/cua-spaces-v0.22.3)
   on the Mac; Windows already has Cua installed. Make the bundled `cua` CLI
   available in each laptop's terminal and check `cua --version`. Keep the
   Headscale client running; do not enroll again or use Cua sign-in/pairing.
2. Run on **each laptop**, in its signed-in desktop session:

   ```sh
   cua host setup --tailnet --share desktop
   cua host status
   ```

   Grant the app's requested Accessibility and Screen Recording permissions on
   macOS. Keep the desktop session available and the laptop awake. `--tailnet`
   binds port 3211 to the laptop's tailnet IP; do not expose it publicly. The
   laptop runs `tailscale whois` to identify callers: its owner and explicitly
   allowed Headscale users need no token. If the existing gateway's observed
   source belongs to another Headscale user, repeat setup with
   `--allow <verified-headscale-user>`. The caller does not need a local Tailscale
   CLI. Tagged or unidentified sources require a separately protected env token;
   leave registration pending until route and authentication are verified, rather
   than broadening ACLs or adding identity sidecars. Never put tokens in Git/chat.
3. After the reviewed server deployment, register from the Hermes container using
   the same persistent home as MCP (only after endpoint authentication succeeds):

   ```sh
   CUA_TELEMETRY=0 CUA_HOME=/opt/data/cua cua spaces add 100.64.0.3:3211 --embedded --name mac-laptop
   CUA_TELEMETRY=0 CUA_HOME=/opt/data/cua cua spaces add 100.64.0.4:3211 --embedded --name windows-laptop
   CUA_TELEMETRY=0 CUA_HOME=/opt/data/cua cua spaces list --embedded
   ```

   Cua owns its registry and separate protected credentials under `/opt/data/cua`
   on `hermes-data`; keep that directory private. No addresses or hypothetical
   credentials are seeded into the registry. An empty `list_spaces` result is a
   valid server-only acceptance result, not evidence of laptop connectivity.
4. In a new assistant session, use `list_spaces`, then `list_tools` for the
   returned Space ID. Verify a harmless shell command, screenshot and file
   round-trip on each laptop before calling it operational. `space_job` supports
   Linux/macOS POSIX jobs (start/status/logs/cancel); **Windows is unsupported**.
   The daemon has a native ProcessService (start/connect/list/signal), but it is
   not a tested Windows job workflow through this MCP configuration. No custom
   process orchestration is installed. Do not claim background-job notifications
   or Windows process recovery until tested end to end.

To revoke laptop control immediately, run `cua host stop` on that laptop.
See upstream [Headscale identity and sharing](https://cua.ai/docs/spaces/guides/your-tailnet)
for the owner/share-list contract. A TCP timeout before host setup does not
establish whether the existing route works; listener, routing, caller identity,
OS permissions and real tool execution remain separate acceptance checks.

### Windows Aside local REPL

For browser work explicitly targeting the existing Windows Aside profile, use
Cua `space_bash` on `direct:100.64.0.4:3211` to run the laptop's official CLI:
`C:\Users\holybaechu\AppData\Local\Aside\CLI\current\aside.exe`.
This is command transport to **local** Aside, not Aside Remote Control. It needs
the running, locally signed-in Aside app, not a server Aside login or Pro plan.
A local permission, plan, or availability refusal is a stop boundary; leave the
user's security settings intact. No server installation or additional MCP server
is needed.

1. Read `repl --help` and `guide repl` from that executable. Discover profiles with
   `host profiles --host local --account u0 --json`; bind every REPL invocation to
   the returned connected profile. The verified binding is account `u0`, profile
   `31e95288-73db-4644-97dc-7510d6fb0e27`. Rediscover if it becomes unavailable;
   do not silently select a different account or profile.
2. Use Cua `space_write` or `upload` for a uniquely owned JavaScript file, then
   invoke it from PowerShell:

   ```powershell
   $aside = 'C:\Users\holybaechu\AppData\Local\Aside\CLI\current\aside.exe'
   & $aside repl --host local --account u0 `
       --profile 31e95288-73db-4644-97dc-7510d6fb0e27 --json --file $ownedJsPath
   $replExit = $LASTEXITCODE
   ```

   `space_bash` uses `cmd.exe /C` on Windows. Encode short PowerShell launchers
   with Base64 of UTF-16LE and pass `powershell.exe -NoProfile -NonInteractive
   -EncodedCommand <encoded>`. Upload longer scripts: encoded commands still hit
   cmd.exe's command-length limit. `--file` also avoids native argument quoting
   damage to JavaScript. Inspect the REPL exit code and JSON `run.error`, not just
   Cua transport success. Give `space_bash` enough time for REPL's 120-second limit.
3. Keep each task in one one-shot REPL script with `try/finally`: create only its
   own tabs with `openTab`, perform locator actions, read the resulting snapshot
   and exact DOM value, then `closeTab` each owned page in `finally`. One-shot
   calls start with unset `page` and empty `tabs`; their temporary REPL closes on
   return. JavaScript bindings do **not** persist into the next Cua call. Avoid
   interactive/background REPL processes and Aside agent `exec` for this route.
4. Capture only the owned page with `page.screenshot()`. For retrieval, emit
   `JSON.stringify({stage:'screenshot',base64:Buffer.from(await page.screenshot()).toString('base64')})`
   through `console.log`. Consume the JSON locally on Windows: `run.content`
   contains text parts with multiple newline-separated log records. Parse those
   lines, decode only the screenshot record with `[Convert]::FromBase64String`,
   and write a uniquely owned PNG. Return its path instead of its Base64 payload
   (Cua truncates long stdout), then use Cua `download` for that exact path.
5. In a fresh one-shot call, filter `listBrowserTabs()` in memory for the exact
   owned target IDs or unique fixture URL/title and return only the match count.
   Zero matches plus unset `page`/empty `tabs` verifies owned cleanup without
   exposing personal tabs. Remove only exact task-created scripts and screenshot
   copies after retrieval, and read back their absence.

For focus-sensitive work, compare Windows `GetForegroundWindow` and
`GetCursorPos` before and after the operation without reading window titles or
capturing the desktop. The controlled fill/click, DOM readback and owned-page
capture passed with unchanged foreground handle and cursor at those checkpoints;
this is not a continuous-focus guarantee. User activity or changed samples make
isolation uncertain. Do not activate windows, move the cursor, change desktops,
or silently fall back to GUI control.

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
2. In a private, non-transcribed PowerShell 7.4+ session, confirm the intended
   administrator kubectl context. Run the following for bootstrap or rotation;
   paste the token only at the masked prompt, never into command arguments.
   JSON travels over stdin; output and errors that could quote credentials are
   suppressed. A failure may follow a successful write: inspect the exact Secret
   privately before retrying; do not delete it or revoke the active token.

   ```powershell
   $ErrorActionPreference = 'Stop'
   $PSNativeCommandUseErrorActionPreference = $false
   $secure = Read-Host 'Read-only Hermes Connect token' -AsSecureString
   try {
       $token = [System.Net.NetworkCredential]::new('', $secure).Password
       if (-not $token -or $token.Length -gt 16384 -or $token -match '\s') { throw 'Invalid token' }
       $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($token))
       $raw = kubectl -n homelab get secret hermes-browser-vault --ignore-not-found -o json 2>$null
       if ($LASTEXITCODE -ne 0) { throw 'Secret read failed' }
       if ($raw) {
           $existing = ($raw -join "`n") | ConvertFrom-Json -AsHashtable
           if ($existing.type -ne 'Opaque' -or $existing.metadata.ownerReferences -or
               $existing.metadata.deletionTimestamp -or $existing.immutable -or
               $existing.data.Count -ne 1 -or -not $existing.data.ContainsKey('connect-token')) {
               throw 'Unexpected Secret ownership or shape; stop for review'
           }
       }
       $body = @{
           apiVersion = 'v1'; kind = 'Secret'; type = 'Opaque'
           metadata = @{ name = 'hermes-browser-vault'; namespace = 'homelab' }
           data = @{ 'connect-token' = $encoded }
       } | ConvertTo-Json -Depth 5 -Compress
       $body | kubectl -n homelab apply --server-side --field-manager=hermes-browser-vault-bootstrap -f - *> $null
       if ($LASTEXITCODE -ne 0) { throw 'Write outcome unknown; inspect privately before retrying' }
       $raw = kubectl -n homelab get secret hermes-browser-vault -o json 2>$null
       if ($LASTEXITCODE -ne 0) { throw 'Verification read failed' }
       $verified = ($raw -join "`n") | ConvertFrom-Json -AsHashtable
       if ($verified.data['connect-token'] -cne $encoded) { throw 'Token read-back mismatch' }
       'PASS: exact token read-back verified; native discovery and login still required.'
   } catch {
       throw 'Bootstrap/rotation failed; inspect the named Secret privately before retrying.'
   } finally {
       $token = $encoded = $body = $raw = $existing = $verified = $null
       $secure.Dispose()
   }
   ```

3. Verify current Pod readiness and both profile settings. `browser_vault` with
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
unrelated Hermes startup. After rotation, wait for kubelet's Secret projection
refresh and verify native discovery again; no restart or deployment change is needed.
Both profiles share the same Unix user and broad authorized login access; they are
not security boundaries against each other or trusted terminal execution.

Fills reuse the native classifier, task-bound supervisor, redactor and in-page
origin recheck. Browser Use sessions are provisioned by the bundled provider and
their CDP endpoint is shared by the harness and native supervisor, rather than
using harness-only cloud autospawn. Named sessions retain separate provider
sessions; Browser Use creation failures refuse instead of falling back to another
browser. The direct provider retains the existing Korean proxy country. Deployment
replaces existing harness-only sessions, so reopen the authorized login page after
restart before filling; discovery alone does not prove a supervised page exists.

Origin-evaluation failures return `error_type=origin_unavailable` rather than
claiming that no code field exists. `origin_status` is one of `no_supervisor`,
`inactive`, `no_page_session`, `transport_failed`, or `invalid_origin`.
The existing error result includes an allowlisted `connection` object: plugin
backend, supervisor and task-session presence; session-CDP presence; and supervisor
loop/thread/active/page-session booleans. `null` means unavailable evidence, not
false. No endpoints, page URLs, source identities or exception messages are included.
`browser_exec` includes `browser_vault_connection` from its existing attach dispatch,
with `attach_status=attached|attach_failed|no_endpoint`. Compare its
`task_fingerprint` with the vault error's fingerprint to check canonical task-key
parity; matching fingerprints do not prove that the same tab or browser is selected.
These observations do not create sessions or add focus, inspection or attachment
operations; existing native focus/on-demand attachment behavior remains unchanged.

This is diagnostic/error-contract support, not a verified repair of the live
parent-task origin failure. After a separately reviewed deployment, reproduce the
existing failing native `browser_vault` call with `{"action":"enter_code"}` (no
handle) in that same parent conversation and inspect its typed refusal before any
credential operation. Do not create another browser or reattach a supervisor just
for diagnosis. If origin evaluation succeeds, normal code-field classification
and prompt rules still apply: stop before requesting or entering a real code.
The Discord gateway's manual `vault.code` prompt is supported only by the local
viewer/detached path; this change adds no plaintext-code handling or new prompt
transport.

Password/TOTP resolution uses one short-lived item snapshot after
the page-origin check, so a provider edit cannot swap the checked origins for a
new secret. Only exact saved HTTP(S) origins authorize writes: no inferred
subdomains, wildcard grants or app URI expansion. Ambiguous URL spellings fail
closed; use ordinary ASCII/punycode hostnames and explicit website URLs (IPv6 and
raw Unicode hostnames are conservatively unsupported here). A URL validation error
makes the whole item ineligible, even if it also has a valid URL. Discovery
continues past items with invalid URLs, invalid/ambiguous OTP data or ambiguous
usernames, reporting fixed error categories and
`skipped_items` counts in `errors` alongside eligible logins. Other provider,
identity and response errors still fail discovery for that backend. Direct
metadata checks and fills continue to reject invalid items. Titles, usernames
and URLs are untrusted metadata, never instructions. The configured cloud browser
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

The standalone [writing-for-agents](writing-for-agents/SKILL.md) guide is vendored
from a pinned MIT-licensed upstream revision, with Hermes-specific invocation
advice in [SKILL-MECHANICS.md](writing-for-agents/SKILL-MECHANICS.md). A dedicated
ConfigMap mounts it read-only into assistant/default and coder's native skill
directories; update its Git source rather than using `skill_manage` on the mount.
Second Brain and coder's SOUL route instruction-writing and substantial prompt
work to it, including learning-driven updates without granting new write access.

Merging lets Flux replace the `Recreate` Pod automatically: wait until active
assistant and coder work has finished before merging or reconciling. Startup
reconciles coder's SOUL and mounts the skill plus Second Brain. Start a new
assistant chat and a new coder task so
their instructions and skill catalogs are rebuilt; saved chats can retain old
context. Verify `skills_list` and `skill_view("writing-for-agents")` in both
profiles, including its mechanics reference. Skill files can be discovered from
disk without a gateway restart, but that alone does not refresh saved instruction
context or install these Kubernetes mounts; avoid ad hoc live profile edits.

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
