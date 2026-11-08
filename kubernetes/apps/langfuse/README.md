# Langfuse

Private Hermes traces at `https://langfuse.${DOMAIN}`, with Authentik SSO-only
sign-in. Credentials remain in the `Langfuse` Secure Note in the `Homelab` 1Password
vault. The ingestion endpoint is
`http://langfuse.homelab.svc.cluster.local:3000`.

The [official Helm chart](https://langfuse.com/self-hosting/deployment/kubernetes-helm)
manages Web and Worker through Flux. Its bundled stores are disabled: the
`langfuse` deployment retains PostgreSQL, ClickHouse, Redis and MinIO on the
existing PVC, reached through the internal `langfuse-backends` Service. This
avoids introducing a ClickHouse operator or migrating database engines and
object storage. Backend ports have no ingress or host exposure.

The three pods request about 2.8 GiB and have container memory limits totaling
7.25 GiB; these are budgets, not measured idle usage. Use the node's kubelet
stats summary for measurements; the cluster has no Metrics API for `kubectl top`.
No high availability or automatic backup is configured.

Worker pools and ClickHouse query parallelism are bounded for this small
deployment; large aggregations can spill to disk. When increasing ingestion,
check queue backlog and dashboard latency before raising concurrency or memory
budgets together.

## Setup and capture

The 1Password Operator synchronizes the existing item into `langfuse-connect`.
Web, Worker and all four backends explicitly select its lowercase-hyphen keys;
notes and the legacy `database-url` are not injected. Headless initialization creates
the Homelab organization, Hermes project and API keys. The existing owner signs
in through Authentik; bootstrap user credentials are no longer stored or injected.
Public sign-up is disabled. The intended retention is **30 days for new traces**,
including their observations, scores and media. This is a staged policy, not an
effective TTL: the current OSS project has no retention configured and keeps data
indefinitely. Automatic [retention](https://langfuse.com/docs/administration/data-retention)
requires self-hosted Enterprise and applies to the whole project, including old
history. Do not set project retention or add a deletion job to approximate a
new-traces-only policy. A supported implementation must preserve existing history
and stay scoped to Hermes; obtain a separate migration decision before enabling
any policy that would expire historical data. No history is purged by this change.

Authentik's Langfuse application grants access through the existing Personal
group and admin entitlement. The native
[Authentik OIDC provider](https://langfuse.com/self-hosting/security/authentication-and-sso) links the
existing Langfuse account by its matching email, preserving its organization,
project access, and API keys. Keep public signup disabled. Email/password sign-in
is disabled with `AUTH_DISABLE_USERNAME_PASSWORD=true`; access depends on
Authentik. Preserve its configuration and the existing Langfuse user when
restoring access. Do not create a replacement owner or regenerate the existing
salt/encryption keys.

Hermes uses the official catalog plugin at a fixed revision and the pinned SDK.
The desired process-wide default is `HERMES_LANGFUSE_CAPTURE=metadata`, with sender
attribution disabled: content is replaced by shapes/sizes while IDs, timings,
usage and tool names remain. Command/file/full-conversation export is not enabled.
`MAX_CHARS=2000` and `MAX_DEPTH=3` bound the plugin's content values during an
explicit, temporary sanitized diagnostic process; they are not whole-trace byte
quotas. Sanitized mode also limits collections and the message window, redacts
secret patterns before truncation and replaces binary/media content. It is not a
general PII detector. Do not enable `full` or broaden an ongoing conversation's
capture for a retrospective question. Provider authentication and Discord tool logs
continue through their existing paths. Missing plugin dependencies, keys or a
failed backend disable/drop telemetry without stopping responses.

The assistant and coder both enable the same prepared plugin and SDK; coder's
plugin directory links to the pinned read-only runtime, not another telemetry
implementation. `prepare-langfuse` scopes the two Operator project credentials
and internal endpoint to coder through Hermes' protected native secret writer;
the plugin's supported endpoint input is environment-based, including for routed
profiles that deliberately cannot borrow the assistant's process credentials.
Removal of either key clears both coder keys and the endpoint as well as the
assistant's cached MCP header. No other profile receives credentials or mutations.

New profiles do **not** implicitly inherit tracing from a traced parent. For a
future authorized specialist, explicitly enable the same pinned plugin, provision
its scoped project credentials/internal endpoint, keep attribution off and inherit
the deployment's metadata capture policy. Do not clone unrelated persona, history,
skills or credentials merely to gain tracing. Verify a real child generation and
tool span after activation, not just `plugins.enabled`.

This deployment supports text traces. MinIO is accessible only inside the cluster;
direct browser media uploads and batch exports are not configured.

## Ask Hermes about past sessions

The assistant connects to Langfuse's native project MCP endpoint with an explicit
allowlist of observation/schema and metrics reads. No prompt, score, dataset or
other write tools are exposed. This is a client tool restriction, not a read-only
API key: the existing project keys still authorize ingestion. The official
Agent Skill/CLI is an alternative for shell workflows; the native MCP avoids a
second package installation and gives the assistant directly discoverable,
filtered tools.

Ask, for example: “Find the ESO to 1Password Operator migration session and
explain avoidable retries, separating necessary safety gates from wasted work.”
Service names are optional: questions about slow work, retries, token waste or
delegation bottlenecks route through the owning Second Brain guidance. Start with
the exact project/session/period and the smallest useful evidence; an adequate
local answer needs no Langfuse call. Hermes can resolve the session ID with its local session search, then filter
`listObservations` by `sessionId`. Inspect schemas first, use explicit time
windows of at most 14 elapsed days, follow cursors, and fetch full payloads only
for selected observations. Cite trace/observation IDs and distinguish measured
latency from causal inference. Local transcripts are a separate fallback, not
proof that Langfuse captured a session. Missing or truncated historical payloads
cannot be reconstructed by enabling tracing now. Delegated work can have a
separate session ID. The official plugin creates a parent `Subagent` span whose
`child_session_id` joins to the child's root `sessionId`; child traces are separate,
not necessarily nested under the parent trace. Verify that join and the parent's
span/root IDs by API readback. Historical routed child transcripts may live in
the parent's shared gateway store with `profile_name=default`, even if coder's
standalone database is empty. Follow explicit child IDs there before claiming no
local records. Disabled tracing means missing telemetry, not missing local history.

At startup, `prepare-langfuse` derives the Basic header from the same Operator
project-key fields using Hermes' native secret writer. Only this derived secret
is stored in the assistant's protected `.env`; Git holds its environment
reference. Removal of either project key removes the cached header. Reloader
refreshes it on the normal Secret-triggered pod restart. The internal request
preserves the public `Host` for Langfuse's MCP host validation; credentials and
observations stay on the existing in-cluster service path. Do not expose that
HTTP endpoint outside the cluster or disable host validation.

The MCP list is reconciled through `hermes config set` at startup and can also
be activated with that CLI without restarting active conversations. Changes to
the generated bootstrap ConfigMap/pod template should only be deployed after
active tasks drain. Verify a real observation read, not just tool discovery;
verify a later ordinary Hermes turn appears to check continuing ingestion.

`hermes chat` on this image connects to the canonical gateway: shell environment
overrides do not change that already-running owner's capture mode or secret scope.
Read back `capture_mode` from a new root. Plugin hooks can reload without killing
tasks, but cached failed initialization or an old secret scope can still prevent
coder ingestion. A fresh native Hermes process verified both profiles with metadata
capture; the existing gateway's coder detail remains unverified until a safe owner
activation after tasks drain. The live gateway still reports sanitized capture;
the metadata default and mounted routing guidance are staged for rollout, not live.

## Outcome quality and operator evaluation

Use `hermes_task_outcome_v1`, a categorical score with `failed=0`, `partial=0.5`,
`success=1`. Define observable task acceptance criteria before execution. Success
means all were verified; partial means a useful result with unmet criteria or
coverage gaps; failed means no usable result or a safety violation. Normal task
reports include verification evidence and remaining gaps. They are not automatically
scored, and a completed tool/agent status alone is not quality evidence.

An explicitly authorized operator can use the already-installed official SDK's
`api.score_configs.create` and `api.scores.create` in the Hermes project, attaching
the score to the parent delegation observation (`trace_id`, `observation_id`).
Reuse the existing named config; record bounded acceptance criteria, verification
method, assessor, parent/child session and trace IDs, and limitations in score
metadata/comment. Use a stable score ID for an idempotent submission, then read
that exact ID back. On this v4 events-only deployment, use
`api.scores_v3.get_many_v3(id=score_id, fields="details,subject")` and
`api.observations.get_many`; legacy trace/score reads return 404. Follow cursors.
Do not add score writes to the assistant's MCP allowlist.

The verified deterministic smoke score is operator-script evidence, not independent
human ground truth or a general agent benchmark. Qualitative retrospective answers
require explicit operator/human review against their cited evidence. Preserve later
human corrections as separate annotations rather than overwriting the original
assessment. No automatic LLM judge, scheduled evaluator or new external export is
installed. Compare outcomes alongside root/delegation latency, generation usage,
and available estimated costs. Model-price estimates are not actual subscription
billing; unavailable costs stay unknown, never fabricated zero.

## Credentials and upgrades

Keep the original salt and encryption key in 1Password: replacing them can make
existing encrypted credentials unreadable. PostgreSQL's password initializes
an empty data directory only. Rotate its existing database role and matching
1Password password together. The chart builds its database connection from
individual fields; the legacy `DATABASE_URL` field is retained for rollback.
Project API keys are also creation-time settings; changing `LANGFUSE_INIT_*`
does not rotate existing records. Change them in Langfuse and update 1Password
to match.
ClickHouse, Redis and MinIO reload their configured credentials on restart.

The administrative metadata and exact source/replacement/ESO byte gate passed
before cutover. Reloader keeps observing the explicitly referenced replacement;
Operator auto-restart stays disabled. Keep ESO's `langfuse-env` for rollback and
its field-ID references unchanged. To roll back, restore the Secret names and
key mappings in **both** `release.yaml` and `resources.yaml` together, without
changing credentials or storage. Hermes independently projects the two
`hermes-project-*` keys from `langfuse-connect`, retaining the optional project-key
behavior. Its coordinated cutover leaves ESO's `hermes-langfuse` for rollback;
see the [Hermes activation boundary](../../platform/onepassword/README.md#remaining-hermes-n8n-and-signing-source-batch).

Web and Worker must use the same release. Back up data before upgrades because
startup runs database migrations; an older image may not support the migrated
schema. See the [upstream deployment](https://langfuse.com/self-hosting) and
[headless initialization](https://langfuse.com/self-hosting/administration/headless-initialization).

The Hermes launcher resets `PYTHONPATH`. A read-only `.pth` file registers the
prepared SDK directory through its standard Python site processing. When
upgrading the pinned Hermes image, verify that its Python site directory still
matches the `python3.14` mounts in `kubernetes/apps/hermes/resources.yaml`.
Bootstrap checks SDK imports through this same site path before enabling tracing.

When switching from the earlier co-located deployment, take a cold backup first.
The app version, backend images, PVC name and subdirectories stay unchanged;
only Web/Worker move into chart-managed pods. The existing `langfuse` Service
continues to serve the same ingestion URL. Verify login, existing traces and one
synthetic trace after reconciliation. To roll back, suspend the HelmRelease and
stop its Web/Worker deployments. Restore the earlier co-located Deployment,
ConfigMap and Service selector while keeping the HelmRelease suspended. Remove
the HelmRelease only after the old co-located app is healthy.

## Backup, restore and removal

`langfuse-data` contains `postgres`, `clickhouse`, `clickhouse-logs`, `redis` and
`minio` subdirectories. It uses local-path storage on this node, with no enforced
capacity quota. Monitor disk use as well as trace retention.

For a consistent cold backup, suspend Flux apps reconciliation and the
`langfuse` HelmRelease. Scale `langfuse-web` and `langfuse-worker` to zero, then
scale `deployment/langfuse` to zero. Wait for all three pods to terminate. Copy
the entire PVC to storage outside this node, preserving numeric ownership and
permissions; retain the matching 1Password item separately. Resume the deployment and Flux
afterward, starting backends before Web/Worker. A copy of only PostgreSQL omits
traces and pending ingestion objects.

To restore, keep the deployment stopped, restore every subdirectory from the
same backup into an empty PVC, restore the matching secret references and image
versions, then start the deployment and verify project access and a synthetic
trace. Never mix stores from different backup times or initialize fresh secrets
over an existing backup.

The PVC has Flux pruning disabled, so removing the app retains its data. An
explicit PVC deletion can still delete the local-path volume and all traces.
Back up first, then delete retained storage separately only when intended.
