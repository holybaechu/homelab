# Homelab backups

This layer is **inactive**: Flux `backup` and both CronJobs are suspended.
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
   complete snapshot is uploaded independently to each repository. Failed restic
   uploads are never tagged `complete`; a failed destination does not prevent an
   attempt at the other. Restore only snapshots tagged `complete`.

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

## Activation and credentials

The existing official 1Password Operator must own `backup-b2-connect` and
`backup-scaleway-connect` in `homelab`. Each new **separate** source item needs exactly
these lowercase field labels/Secret keys (no broad Connect token):

| Key | Meaning |
| --- | --- |
| `access-key-id` | B2 bucket-scoped or Scaleway dedicated-project application access ID |
| `secret-access-key` | Corresponding S3 secret |
| `restic-password` | Independent securely generated repository password; retain outside the cluster for disaster recovery |

Set the two `BACKUP_*_CONNECT_ITEM_PATH` references to verified item IDs in the
existing Homelab vault. Human-facing item and provider application-key names
require user approval; no item/key is created by this layer. Existing app item
references are unchanged. Securely confirm exact projected key availability,
including n8n's current underscore key, before activation; do not relabel an item
or rotate an original encryption key to make a missing projection work.

B2 is fixed to the verified private bucket `homelab-backup-b2`, endpoint
`s3.us-west-004.backblazeb2.com`, region `us-west-004`; SSE-B2 is supplementary to
restic encryption, not a replacement. Scaleway region, dedicated project, bucket,
application/key and item names are **pending approval**; no OVH names are reused.
Set `BACKUP_SCALEWAY_BUCKET`, `BACKUP_SCALEWAY_REGION` and
`BACKUP_SCALEWAY_REPOSITORY` only after verifying the actual private bucket.
Use `s3:https://s3.<region>.scw.cloud/<approved-bucket>` at the bucket root;
the worker rejects mismatched endpoints, paths, regions and unresolved inputs.
The supported regional allowlist is `fr-par`, `nl-ams`, `pl-waw`; verify
Standard One Zone availability in the selected region before provisioning.

[Scaleway Standard One Zone](https://www.scaleway.com/en/docs/object-storage/concepts/#storage-class)
uses the API token `ONEZONE_IA`, **not** `STANDARD_IA` or `STANDARD`.
The worker passes `s3.storage-class=ONEZONE_IA` to every Scaleway restic command,
including init, config, keys, locks, indexes, snapshots and data pack writes.
Restic 0.19.1 treats only GLACIER/DEEP_ARCHIVE as metadata exceptions; ONEZONE_IA
is applied to all saves. Verify returned classes with real credentials before
activation; local tests do not prove cloud storage behavior.

B2 runtime credentials remain bucket-scoped. Scaleway uses a dedicated IAM
application with project-scoped `ObjectStorageReadOnly`,
`ObjectStorageObjectsWrite` and `ObjectStorageObjectsDelete`, without IAM
management rights. IAM alone also permits object ACL, retention/legal-hold and
bucket Object Lock writes; a private bucket ACL does not prevent public object
ACLs. Require the provisioning root's official `2023-04-17` bucket policy before
issuing a runtime key: only restic list/location/get/put/delete and multipart
actions for the runtime application on the intended bucket, with no ACL,
retention/legal-hold or bucket configuration writes. Explicit verified
provisioner and independent administrator/recovery principals retain access;
confirm their identities/IAM grants before the immediately effective policy is
installed. Do not rely solely on Organization-owner recovery or weaken/remove
the policy after key creation.

This is **not project-wide bucket-only access**: isolate backups in a dedicated
project. The policy does not restrict arbitrary future unprotected buckets there,
and project bucket listing is not claimed to be denied. After authorized
provisioning, read back the exact policy/private ACL and application/key scope,
verify administrator/recovery access, then probe real runtime list/location,
read/write/delete and multipart behavior. Verify rejected object ACL (including
public ACL), retention/legal-hold/Object Lock, other bucket configuration and
other-project access under a separately approved safe probe plan, never on restic
data or by enabling Object Lock on the real bucket. Local tests are not cloud
authorization evidence. Do not enable Object Lock or retention incompatible with
restic deletion. See the official [IAM action mapping](https://www.scaleway.com/en/docs/object-storage/reference-content/s3-iam-permissions-equivalence/)
and [bucket policy semantics](https://www.scaleway.com/en/docs/object-storage/api-cli/bucket-policy/).

No lifecycle rule may expire current objects: old restic packs can still be
referenced by recent snapshots. B2 currently keeps all versions; restic deletes
can leave billed hidden versions. Review a **hidden/noncurrent-only** deletion
rule separately with the provider owner, with no upload-age hiding/expiry of
current objects. Do not apply such a rule without that safety review.
Review current Scaleway Standard One Zone minimum-duration, retrieval and request
pricing with the approved region before activation.

1. Run CI's `backup` workflow. Publish its verified tooling image only through an
   explicitly approved `workflow_dispatch` with `publish=true`; this publishes
   `ghcr.io/holybaechu/homelab:backup-<commit>` but does not deploy it. Pin its
   read-back digest in `BACKUP_IMAGE`; ensure the cluster can pull it. Review
   image/package visibility before publishing any image.
2. Finish provider setup and Operator projections privately. Do not send values
   through chat, logs, Git or command arguments. Independently retain both
   passwords, bucket identities and the original application/Connect recovery
   credentials outside this cluster.
3. With parent approval, unsuspend only Flux `backup`; keep both CronJobs
   suspended. Verify the three workers, watchdog, explicit source mounts,
   least-privilege RBAC, the Deny admission binding and empty policy type-check
   warnings, existing app keys and stage capacity. Standalone CEL tests do not
   replace API-server admission/read-back verification. Local-path requests
   do not reserve/enforce a quota; staging shares the same disk, is not a backup,
   and contains mode-restricted plaintext. Retain PVCs when removing this layer.
4. From an authorized administrator, initialize **each verified empty target**
   explicitly using the worker's projected files and `worker.repository()`
   environment: `kubectl -n homelab exec backup-worker-0 -c worker -- python3
   /backup/worker.py init b2` (then `init scaleway`). Each prints only its repository ID.
   `restic init` is deliberately not an automatic fallback after a
   failed repository read. Never initialize a substitute repository on an auth,
   endpoint or network failure; verify the distinct repository IDs afterwards.
5. With parent approval for the first capture outage, create a Job from
   `cronjob/backup-daily`. Measure stop-to-ready time for every group, inject a
   producer timeout and coordinator interruption, and verify exact original
   counts/suspension restoration, no valid partial snapshot and uploads only
   after resumption. Do not activate a schedule if any drill exceeds ten minutes.
6. Run the isolated restore below against **both real destinations**, then
   perform full application restores into isolated PVCs/services (never production
   volumes). Only then unsuspend daily and maintenance CronJobs in Git.

Daily backup starts at **04:00 Asia/Seoul**. After each successful publication,
`forget` retains **7 daily / 4 weekly / 6 monthly** restore points without pruning
data. Maintenance is monthly at 12:00 on the first, under the same journal lock:
reapply retention, prune without repacking existing packs, then metadata `check`.
This avoids daily
IA rewrite/retrieval churn. It is not a full data scrub; schedule an approved
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
kubectl -n homelab exec backup-worker-0 -c worker -- python3 /backup/worker.py restore-test scaleway
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

A failed Job is the Kubernetes failure signal; no external alert destination is
invented here. Before unattended activation, connect Job failure **and missing
fresh `complete` snapshots at either destination** to the existing operator's
alerting route; if no such route exists, that is an activation blocker, not a
successful alert test. Inspect `backup-state`, Job status and sanitized logs.

If the runner dies while stopped, leave the watchdog running: it restores the
journal's original values after the deadline. If API access failed, it retries
without clearing recovery intent. After verifying no backup/maintenance Job or
worker producer is active, an administrator may reset a stranded non-capture
`preparing`, `upload` or `maintenance` journal to `idle`. **Never reset a journal
with recorded replicas/controls**; recover them first. Do not delete stage/source
PVCs, force-unlock restic or retry against a different bucket as a shortcut.
