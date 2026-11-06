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

[Langfuse](../langfuse/README.md) provides self-hosted tracing. The official
plugin captures sanitized content, with end-user attribution disabled. Monitoring
failure leaves the assistant running. Pattern-based masking can retain private
text such as names or note contents.

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
