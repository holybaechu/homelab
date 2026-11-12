# Homelab backups

This layer is **inactive**: Flux `backup` and all three CronJobs are suspended.
`REQUIRED_*` values are deliberate activation blockers, not working credentials.
Nothing here initializes repositories, rotates keys or stops production on merge.

## Capture and failure boundary

The upstream [K8up application-aware mechanism](https://docs.k8up.io/k8up/how-tos/application-aware-backups.html)
streams commands from running Pods but does not provide the required bounded,
cross-namespace stop/copy/resume transaction. Resticprofile hooks likewise do not
supply an independent Kubernetes recovery watchdog. This layer therefore uses
maintained restic 0.19.1, rsync, native PostgreSQL clients and SQLite's online backup
API, with a small coordinator for that missing boundary. The tooling image is
built in CI, not installed over the network when a backup starts.

1. Read-only source PVC mounts pre-sync to private local staging PVCs. No node
   mounts, application exec, CSI snapshots or changes to shared storage policy.
2. Before each group stops, a compare-and-swap ConfigMap journal records its exact
   original replica counts, affected Flux/Helm suspension states and a deadline.
   The coordinator suspends only the owning reconciliation layers. An already
   Reconciling layer causes an abort rather than racing an upgrade.
3. Only necessary writers stop. All matching Pods must disappear, including
   terminating Pods, before the final sync/dump begins. Frozen producers have a
   local deadline; any error rejects the entire capture.
4. The coordinator restores the recorded replica counts and suspension states,
   then waits for app rollout. A separate always-on watchdog invalidates expired
   captures and retries restoration from the durable journal if the Job dies.
   A group has 420 seconds total capture budget, with producers ending 30 seconds
   earlier, leaving time inside the approved ten minutes for resumption.
5. After **all apps resume**, committed Hermes SQLite state is captured online,
   namespace staging is collected with checked tar producers/consumers, and one
   complete snapshot is uploaded to B2. Failed restic uploads are never tagged `complete`. Restore only snapshots tagged `complete`.

**Availability limit:** no process on this single Talos node can guarantee a
wall-clock ten-minute service recovery if the node, Kubernetes API or application
startup itself fails. The watchdog guarantees persistent recovery intent and
retries once the control plane works; it cannot defeat a node outage. Do not
claim an unconditional availability guarantee. Initial activation requires a
measured approved capture/recovery drill; if the ten-minute requirement includes
such infrastructure failures, an independent recovery/control-plane design is a
prerequisite, and this layer must remain suspended.

Do not deploy/reconcile app changes, restart workers or run maintenance during a
backup. All scheduled/manual backup and maintenance invocations use the same CAS
journal lock. CronJob `Forbid` alone does not coordinate different CronJobs.
Workers have stable Pod names for resource-name-bound exec permissions. The
watchdog cannot exec, mount sources, read Secrets or create workloads. Neither
service account can exec application Pods, delete PVCs/PVs, read Secrets or create
Jobs/Pods. The runner can scale only the named writers and suspend only
`apps`, `identity`, `access` and the Authentik HelmRelease. A fail-closed admission
policy bounds those two identities to changing only `spec.suspend`, preserving
other spec fields and operator metadata; Flux controller identities are unaffected.
Root can read source
ownership/xattrs, but source PVCs are mounted read-only; capabilities are limited
to local staging metadata preservation.

| Included state | Capture boundary |
| --- | --- |
| OpenCloud config + complete data tree, including completed torrent bodies | Stop OpenCloud **and qBittorrent**, rsync `-aHAX --numeric-ids`, preserving xattrs/sidecars/ownership; cloud upload occurs after restart |
| Immich media + internal DB + roles | Stop Immich writer, PG14 `pg_dump` custom archive and `pg_dumpall --roles-only` first, then final media sync; PG itself stays online |
| Authentik media + internal DB + roles | Stop server and worker, PG18 native dump/roles, then media sync; PG stays online |
| n8n SQLite + filesystem binary data + original encryption key | Stop n8n for the binary-directory boundary, SQLite Online Backup API, exact `n8n-connect/encryption_key` projection matching the current workload |
| Zipline PGlite + uploads + original secrets | Stop only during physical local capture; never open PGlite from a second process; omit `.tmp` uploads |
| Headscale SQLite/noise key + gateway identity | Stop Headscale and gateway together; SQLite Online Backup API plus gateway state; resume original counts together |
| T3 server state/auth/chats/diffs/workspaces/unpublished work | Stop T3 and all its child processes, copy `t3-data`, preserve Git metadata shared by worktrees, materialize SQLite backups; resume before upload |
| Hermes sessions/profiles/auth/vault/uncommitted workspaces/recovery | Never stop Hermes; selected filesystem capture plus Online Backup API for every discovered selected SQLite DB, including WAL-committed transactions; uncommitted/in-flight actions are interrupted recovery, not a global point-in-time snapshot |

No standalone MySQL, AdGuard, Collabora proofkey, qBittorrent configuration/resume
state, Langfuse, Kutt, incomplete downloads, model/tool caches or temporary files
are selected. Generated Hermes GitOps configuration is excluded. For the exact
authoritative homelab Git origin, clean tracked files already present in
`origin/main` are omitted and recorded in `hermes-repositories.json`; restore
those from that commit before overlaying workspace recovery. The manifest also
records tracked deletion tombstones, which must be applied after the overlay
(see below), preserving both staged and unstaged deletions. Dirty files,
untracked files, unpublished commits, index/stash recovery and other repositories
are retained. Git secret references are **not** key values: original runtime
keys remain in 1Password and required recovery keys are explicitly projected into
the encrypted backup. Never replace them with fresh generated keys on restore.

T3 is a bounded stopped capture, unlike the online Hermes capture. Schedule it
only when T3 threads, delegates and terminals are idle; this backup includes no
T3 task drain protocol. Never start a manual capture from an active T3 thread.
Use an independent operator session for the first drill. Pre-sync may run online,
but the final copy and Git inventory run only after T3 Pods have disappeared.
T3 rebuildable caches and clean tracked homelab files are omitted; server state,
provider authentication, grants, attachments and unpublished work remain selected.
`t3-repositories.json` records reconstruction commits and deletion tombstones.
All T3 Git metadata remains selected, even for clean main checkouts: linked
worktrees can need their shared objects and unpublished refs for recovery.

## Activation and credentials

This deployment uses **Backblaze B2 only**. A second destination is deferred;
there is no Scaleway Operator item, required Secret, endpoint or scheduled upload.
The worker retains its previously tested optional Scaleway command support, but
this deployment does not configure or invoke it.

The existing official 1Password Operator owns `backup-b2-connect` in `homelab`.
Its separate source item needs these lowercase field labels/Secret keys:

| Key | Meaning |
| --- | --- |
| `access-key-id` | B2 bucket-scoped application key ID |
| `secret-access-key` | Corresponding application key |
| `restic-password` | Repository password; retain outside the cluster for disaster recovery |

Set `BACKUP_B2_CONNECT_ITEM_PATH` to the verified item ID in the existing Homelab
vault. The approved runtime key/item title is `homelab-backup-b2`; this layer
creates neither. Existing app references and original encryption keys must stay
unchanged. Securely confirm projected keys, including n8n's current underscore
key, before activation. Never relabel or rotate an original key to fix projection.

The private bucket is `homelab-backup-b2`, endpoint
`s3.us-west-004.backblazeb2.com`, region `us-west-004`. Runtime capabilities are
only `listBuckets`, `listFiles`, `readFiles`, `writeFiles`, `deleteFiles`, restricted
to that bucket, with no prefix or global bucket-name listing. Bucket configuration
and key-management authority remain outside the cluster. Verify actual positive
and negative runtime scope before activation. SSE-B2 supplements restic encryption.

No lifecycle rule may expire current objects: old restic packs can still be
referenced by recent snapshots. B2 currently keeps all versions; restic deletes
can leave billed hidden versions. Review a **hidden/noncurrent-only** deletion
rule separately with the provider owner, with no upload-age hiding/expiry of
current objects. Do not apply such a rule without that safety review.

1. Run CI's `backup` workflow. Publish its verified tooling image only through an
   explicitly approved `workflow_dispatch` with `publish=true`; this publishes
   `ghcr.io/holybaechu/homelab:backup-<commit>` but does not deploy it. Pin its
   read-back digest in `BACKUP_IMAGE`; ensure the cluster can pull it. Review
   image/package visibility before publishing any image.
2. Finish provider setup and Operator projections privately. Do not send values
   through chat, logs, Git or command arguments. Independently retain the B2
   password, bucket identity and the original application/Connect recovery
   credentials outside this cluster.
3. With parent approval, unsuspend only Flux `backup`; keep all three CronJobs
   suspended. Verify the three workers, watchdog, explicit source mounts,
   least-privilege RBAC, the Deny admission binding and empty policy type-check
   warnings, existing app keys and stage capacity. Standalone CEL tests do not
   replace API-server admission/read-back verification. Local-path requests
   do not reserve/enforce a quota; staging shares the same disk, is not a backup,
   and contains mode-restricted plaintext. Retain PVCs when removing this layer.
4. From an authorized administrator, initialize the **verified empty B2 target**
   explicitly using the worker's projected files and `worker.repository()`
   environment: `kubectl -n homelab exec backup-worker-0 -c worker -- python3
   /backup/worker.py init b2`. It prints only its repository ID.
   `restic init` is deliberately not an automatic fallback after a
   failed repository read. Never initialize a substitute repository on an auth,
   endpoint or network failure; record and independently retain its repository ID afterwards.
5. With parent approval for the first capture outage, create a Job from
   `cronjob/backup-daily`. Measure stop-to-ready time for every group, inject a
   producer timeout and coordinator interruption, and verify exact original
   counts/suspension restoration, no valid partial snapshot and uploads only
   after resumption. Do not activate a schedule if any drill exceeds ten minutes.
6. Run the isolated restore below against **the actual B2 repository**, then
   perform full application restores into isolated PVCs/services (never production
   volumes). Only then unsuspend daily, maintenance and health CronJobs in Git.

Daily backup starts at **04:00 Asia/Seoul**. After each successful publication,
`forget` retains **7 daily / 4 weekly / 6 monthly** restore points without pruning
data. Maintenance is monthly at 12:00 on the first, under the same journal lock:
reapply retention, prune without repacking existing packs, then metadata `check`.
This limits unnecessary repacking. It is not a full data scrub; schedule an approved
paid full/subset read check when needed. Pending/unpublished snapshots are never
valid restore points and are not automatically forgotten; inspect and remove
them explicitly after failed uploads to avoid orphan-storage growth.

## Restore and incident operation

Use an authorized operator account; do not broaden the interactive Hermes account
just to exec. The small real restore path downloads n8n's database and original
key into a temporary staging subdirectory, verifies restored bytes and SQLite
integrity/schema, then deletes only that isolated directory:

```sh
kubectl -n homelab exec backup-worker-0 -c worker -- python3 /backup/worker.py restore-test b2
```

This is more than `restic check`, but does not prove n8n credential decryption or
all application restores. For full recovery, use a `complete` snapshot, restore
into **new isolated** claims, preserve numeric IDs/xattrs, restore original
1Password values first, then start the pinned app versions. For PostgreSQL,
restore `roles.sql` with the matching native major client before the custom
`database.dump`; review bootstrap-role collisions rather than ignoring all SQL
errors. Immich needs its pinned vector extensions. Restore OpenCloud config and
data together, Zipline data/uploads together, and Headscale/gateway identities
from the same snapshot. Do not attach a second live PGlite process or production
OIDC/webhook ingress to a restore drill. Rebuild excluded caches/indexes and
GitOps-managed configuration after recovery. For Hermes Git recovery, reconstruct
repositories at each recorded commit, then overlay the captured `hermes-data`
and `hermes-repositories.json` into an isolated directory below `/stage/restore/`.
Before inspecting or starting the restored workspace, run:

```sh
python3 /backup/worker.py restore-git-deletions /stage/restore/<drill>
```

This applies verified tracked-file tombstones without changing the recovered
Git index. It rejects traversal, symlinks, untracked targets and directories;
never point recovery tooling at production/user workspaces.

For T3, reconstruct the listed clean files at each `t3-repositories.json` commit
before overlaying `t3-data` and that manifest in a new isolated restore directory.
Apply its tombstones with:

```sh
python3 /backup/worker.py restore-git-deletions /stage/restore/<drill> t3-data
```

Preserve shared Git metadata and unpublished refs. Before starting a restored T3,
verify SQLite integrity, private file ownership and auth/grants without printing
secrets. Restore at `/var/lib/t3` in the isolated environment, or repair Git
worktree pointers there before use; never reuse production volumes or connect
recovered agents to production. Verify an unpublished commit, staged and unstaged
changes, tracked deletions, attachments and actual chat state in the restore drill.

Backup and maintenance failures notify the existing Hermes Discord home channel
through Hermes's native `deliver_only` webhook; no agent run or new notification
service is involved. The hourly `backup-health` CronJob checks the actual B2
repository for a `complete` snapshot newer than 24 hours, without repository locks
or cache writes. An unreadable repository is an alert, not freshness success.
Repeated identical health alerts are suppressed for 24 hours, resetting after a
fresh snapshot. Suppression is recorded only after a successful direct Discord
delivery response; an accepted webhook or duplicate response is not delivery proof.

Only `n8n-connect/hermes_webhook_secret` is projected for the existing shared
webhook authentication. The health Pod has no Kubernetes API token or application
source mounts. No credential values, paths, database contents or native tool
errors enter alerts. The route remains cluster-only. Both checks and delivery run
on this cluster; they do not provide an independent alert during a whole-node outage.

Before unattended activation, roll out the reviewed Hermes route while Hermes is
idle, then send one explicit test notification with a uniquely named manual Job:

```sh
kubectl -n homelab create job backup-alert-test --from=cronjob/backup-health --dry-run=client -o json |
  jq '.spec.template.spec.containers[0].command = ["python3", "/backup/health.py", "test"]' |
  kubectl apply -f -
```

Verify the actual test message in the home channel and Job success. Also exercise
a controlled backup failure and the missing/unreadable-repository paths before
unsuspending the schedules. Inspect `backup-state`, Job status and sanitized logs
when delivery fails; do not treat failed notification jobs as successful alerts.

If the runner dies while stopped, leave the watchdog running: it restores the
journal's original values after the deadline. If API access failed, it retries
without clearing recovery intent. After verifying no backup/maintenance Job or
worker producer is active, an administrator may reset a stranded non-capture
`preparing`, `upload` or `maintenance` journal to `idle`. **Never reset a journal
with recorded replicas/controls**; recover them first. Do not delete stage/source
PVCs, force-unlock restic or retry against a different bucket as a shortcut.
