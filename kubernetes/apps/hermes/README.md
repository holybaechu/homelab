# Hermes

Personal assistant with TickTick, a synced Obsidian vault, and existing homelab
services. Flux applies this app from the apps layer. Credentials remain in
1Password and arrive through External Secrets; model authentication and the live
profile remain on `hermes-data`.

## Behavior and search

[second-brain.md](second-brain.md) owns source selection, knowledge capture, and
answer scope. Discord tool logs stay visible; final answers focus on useful
results. [QMD](qmd/README.md) supplies local keyword and semantic search over a
read-only vault mount. Reopen source notes before relying on indexed results.

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
the official plugin files use SHA-256 checks. Plugin selection is seeded only
for a new profile; manage an existing profile with Hermes's native configuration.
