# Langfuse

Private Hermes traces at `https://langfuse.${DOMAIN}`, with native sign-in using
the `Langfuse` item in the `Homelab` 1Password vault. The ingestion endpoint is
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

External Secrets reads the existing `LANGFUSE_*_REF` fields from
`kubernetes/clusters/homelab/onepassword.yaml`. Headless initialization creates
the Homelab organization, Hermes project and initial owner. Public sign-up is
disabled. The OSS deployment retains traces until manually deleted; automatic
[retention](https://langfuse.com/docs/administration/data-retention) requires the
self-hosted Enterprise edition.

Hermes uses the official catalog plugin at a fixed revision and the pinned SDK,
with `HERMES_LANGFUSE_CAPTURE=sanitized` and sender attribution disabled.
Questions, answers, system context and tool content can appear in traces after
secret-pattern redaction and size limits. Names, email addresses and private
notes can remain; this filter is not a general personal-information detector.
Use `metadata` to omit content. Provider authentication and Discord tool logs
continue through their existing paths. Missing plugin dependencies, keys or a
failed backend disable/drop telemetry without stopping responses.

This deployment supports text traces. MinIO is accessible only inside the cluster;
direct browser media uploads and batch exports are not configured.

## Credentials and upgrades

Keep the original salt and encryption key in 1Password: replacing them can make
existing encrypted credentials unreadable. PostgreSQL's password initializes
an empty data directory only. Rotate its existing database role and matching
1Password password together. The chart builds its database connection from
individual fields; the legacy `DATABASE_URL` field is retained for rollback.
Initial owner password and project
API keys are also creation-time settings; changing `LANGFUSE_INIT_*` does not
rotate existing records. Change them in Langfuse and update 1Password to match.
ClickHouse, Redis and MinIO reload their configured credentials on restart.

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
