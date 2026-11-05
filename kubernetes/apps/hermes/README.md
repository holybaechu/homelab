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
applies only the spans in [skill-overrides.json](skill-overrides.json). Existing
custom paragraphs, supporting files, and relocated skills are preserved. Missing
or ambiguous anchors stop startup before any override is written; review the
changed skill against the pinned image rather than resetting the skills folder.
An I/O failure can leave earlier files updated; each file replacement is atomic
and rerunning is idempotent. Inspect the `reconcile-skills` init-container log.

Modified skills are treated as user-owned by native sync. Review their upstream
changes when upgrading the Hermes image. JSON anchors encode literal dollar
characters as `\u0024` so Flux substitution preserves them. To undo overrides,
stop the gateway and apply a temporary copy with the listed `old` and `new`
values exchanged; reverting Git alone does not undo edits on the persistent volume.

Run the focused synthetic checks with:

```sh
python -m unittest discover -s kubernetes/apps/hermes/tests -p 'test_reconcile_skills.py'
python -m unittest discover -s kubernetes/apps/hermes/langfuse -p 'test_*.py'
```

The QMD refresher check requires Linux; see its guide. Test fixtures use invented
data and must stay separate from the synced vault and real tasks.
