# Vault search

[QMD](https://github.com/tobi/qmd) adds local keyword and semantic retrieval to
Hermes. It reads the synced vault at `/vault`; Obsidian remains the source of
truth. The MCP endpoint binds to the pod's loopback address and has no Service
or ingress.

The Hermes bootstrap installs the locked Linux x64 CPU runtime in `/opt/qmd`.
Models and the derived SQLite index persist under `/opt/data/qmd`; the default
models need about 2.1 GiB before the index. Model revisions are pinned in
[index.yml](index.yml). Install with `npm ci --omit=optional --ignore-scripts`:
the two explicit platform dependencies keep the CPU and SQLite vector binaries
while skipping optional GPU packages and install scripts.

The `qmd-index` container scans Markdown files and embeds changes every five
minutes after the previous pass finishes. The first model download and full
embedding pass take longer. A failed pass leaves the current derived data in
place and retries at the next interval; keyword and vector freshness can differ
until embedding succeeds. Inspect `kubectl -n homelab logs deploy/hermes -c
qmd-index` and `/state/cache/qmd/last-refresh` in that container when checking
refresh failures.

Search results and QMD `get` are indexed snapshots. Resolve the returned
`vault/<relative-note>` against Hermes's configured vault root
(`/opt/data/obsidian/vault`) before reading it as evidence, especially after
edits or deletion.
An empty vector result during initial indexing or a failed embedding pass does
not establish that a note is absent; check QMD `status` and use the existing
vault search when needed.

Restarting the pod reinstalls the pinned runtime and preserves the index and
models. To rebuild derived data, stop the QMD containers before removing only
their index files under `/opt/data/qmd/cache/qmd`, then restart the pod. Keep the
vault, model files, and the rest of `hermes-data` intact. Changing the embedding
model requires removing its cached model file while QMD is stopped, then a full
`qmd embed --force` pass with the new configuration.

Run the isolated refresher regression check on Linux with
`python -m unittest discover -s kubernetes/apps/hermes/qmd -p 'test_*.py'`.
