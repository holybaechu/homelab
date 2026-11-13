# Homelab backups

[K8up](https://docs.k8up.io/k8up/) owns scheduling, restic uploads, repository
checks and retention. Each application has a `Schedule`, a selected
[`PreBackupPod`](https://docs.k8up.io/k8up/how-tos/prebackuppod.html), and an
independent repository at `homelab-backup-b2/k8up/<namespace>/<app>`.
Only upstream K8up, Python and matching PostgreSQL images are used; there is no
homelab backup image to build or publish.

Flux `backup` and the health CronJob remain suspended until live acceptance.
Merging installs the opt-in K8up controller, but does not create repositories or
capture data. The Hermes Discord route changes the bootstrap ConfigMap shared
with T3; checkpoint both before a merge that rolls them.

## Capture and availability

Backups start at **04:00 Asia/Seoul**. Applications capture and upload independently;
there is no cluster-wide point-in-time snapshot or global upload barrier.
`backup.labelSelectors` selects exactly one helper; source PVCs stay read-only.
Raw database PVCs are never selected by K8up's generic filesystem backup.

| Application | Included state and consistency boundary |
| --- | --- |
| OpenCloud | Config and complete data tree, including completed downloads and original internal keys. Stop OpenCloud and qBittorrent for the final local capture; preserve ownership, hardlinks, xattrs and sidecars. |
| Immich | PG14 custom-format DB dump and roles first, then media, using the existing vector-enabled PostgreSQL image. Immich stays online, following its [online backup order](https://docs.immich.app/administration/backup-and-restore/). |
| Authentik | PG18 custom-format DB dump, roles, media, original DB password and secret key; server and worker stay online. |
| n8n | Stop n8n for a consistent SQLite/filesystem-binary-data boundary; include its original `encryption_key`. |
| Zipline | Stop Zipline for a physical PGlite/uploads capture, excluding `.tmp` uploads. Never attach a second PGlite process to the live directory. Include original core and OIDC secrets. |
| Hermes | Online SQLite backup API for every selected SQLite DB, including committed WAL, plus profiles, auth, sessions, vault and workspaces. Hermes remains available. |
| T3 | Online SQLite backup API plus server/auth/grants, chats, diffs, attachments, workspaces and shared Git metadata. T3 remains available. |
| Headscale | Online SQLite backup API, noise/private keys and original OIDC secret. |
| Headscale gateway | Persistent gateway identity and original gateway auth key. |

Online capture preserves each SQLite DB's committed state. Files, multiple DBs,
and independent applications can represent different instants. In-flight actions
and actively edited workspaces require interrupted-work recovery; this does not
claim a server-wide transaction. A corrupt DB or changed/failed tar producer fails
the backup. PostgreSQL dumps use a native consistent DB snapshot; concurrent media
deletion can still affect DB/file correspondence. Isolated app restores remain
part of acceptance.

Whole Hermes/T3 workspaces and `.git` metadata are included, covering unpublished
commits, index/stash state, tracked deletions and untracked work directly. The old
custom clean-Git-file filtering and reconstruction manifests are removed; this
modestly expands coverage and storage. Standard dependency/tool/model caches,
logs and GitOps-rebuilt Hermes configuration are excluded in `online.py`.
Other applications, incomplete downloads, raw PostgreSQL data, container images
and unrelated caches are excluded. Infrastructure state and its original
1Password passphrase remain in their existing protected recovery location;
these helpers do not collect the Windows recovery material.

Three applications need a small exception to direct streaming: OpenCloud's
[documented stopped backup](https://docs.opencloud.eu/docs/admin/maintenance/backup/),
n8n's DB/binary-file boundary, and Zipline's physical PGlite capture.
`offline.sh` uses the official K8up image's restic, kubectl, bash and jq:

1. Warm a local encrypted restic cache while the app runs. This is staging on the
   same node, not a second backup. Keep only two local snapshots per app.
2. Acquire the resourceVersion-protected `backup-capture-state` journal, recording
   exact replicas and the original Flux `apps` suspension state before mutations.
   Serialize these stop windows; abort if `apps` is already reconciling.
3. Suspend `apps`, stop only that application's writers, and wait until all their
   Pods, including terminating Pods, disappear. The capture budget is 420 seconds;
   the final producer ends 30 seconds earlier and is killed if it exceeds its limit.
4. The single `Recreate` watchdog alone restores recorded replicas and Flux state,
   waits for readiness and acknowledges completion. A dead or expired runner is
   invalidated and recovered from the durable journal. Recovery failures keep the
   journal and retry; they never clear recorded replicas. Captures ready later than
   the total 600-second budget are invalid.
5. Only after successful recovery does native `restic copy` send the exact local
   snapshot to B2. This preserves filesystem metadata without an intermediate tar.
   The K8up stream contains a small receipt with the source snapshot ID.

The stopped apps' **data** snapshots have tags `data,<app>` and hostname `homelab`,
matching K8up's namespace host filter for retention. Receipt snapshots have only
`<app>` and are not data restore points. Other apps stream a tar archive stored as
`/<namespace>-capture.tar` with their app tag. Failed streams can leave unusable
snapshots: require a successful corresponding Backup and native Job before restore.

The ten-minute budget needs a measured live stop-to-ready drill. A watchdog on
this single Talos node cannot guarantee recovery while the node/API is unavailable
or an app cannot start; it preserves recovery intent and retries. Keep scheduling
inactive if the measured normal path exceeds the budget. Avoid app deployments,
manual scales and controller upgrades during stopped captures.

The runner/watchdog can write only the journal, four named Deployment scale
subresources, and Flux `apps.spec.suspend`. The Deny admission policy preserves
all other spec fields and operator metadata. Neither can exec app Pods, read
Secrets through the API, create workloads or delete PVCs. Only stopped helpers
have these scale permissions; online helpers have no API token. `backup-stage`
and the recovery journal are protected from Flux pruning; the journal uses
`IfNotPresent` so reconciliation cannot overwrite active recovery state. The
320Gi local-path request is not a disk quota or capacity reservation.

## B2 and credentials

B2 is the only destination; Scaleway is deferred. Preserve the private bucket
`homelab-backup-b2`, endpoint `https://s3.us-west-004.backblazeb2.com`, region
`us-west-004`, SSE-B2, disabled Object Lock and Keep All Versions.

The existing **Homelab / homelab-backup-b2** 1Password item is projected by the
official Operator into `backup-b2-connect` in all three namespaces. Its verified
ID remains `BACKUP_B2_CONNECT_ITEM_PATH`. Required lowercase keys are
`access-key-id`, `secret-access-key` and `restic-password`. Workloads project only
explicit keys. Preserve original app keys, the restic password and infrastructure
state/passphrase; never rotate or relabel them to fix a projection failure.
Independently retain these credentials outside this cluster for disaster recovery.

The runtime key is restricted to this bucket with `listBuckets`, `listFiles`,
`readFiles`, `writeFiles` and `deleteFiles`; bucket/key administration stays outside
the cluster. K8up performs its standard repository initialization at the configured
app prefix before executing the stream. Verify the exact prefixes and credentials
before the first manual Backup; never change the endpoint/prefix or initialize a
replacement repository as an authentication-error workaround. Retain each
repository ID after its first successful initialization.

Daily backups retain **7 daily / 4 weekly / 6 monthly** points when the monthly
prune runs at 12:00 on the first. Weekly metadata checks run Sunday at 12:00.
Retention is per app/path group; these are overlapping calendar policies, not an
exact snapshot count or byte quota. Metadata checks do not read all object bodies;
a deliberate `restic check --read-data` or subset check is a separate paid scrub.
K8up manages repository locks for its jobs; do not force-unlock a running job.

Keep All Versions means restic prune can leave billed hidden B2 versions. Never
expire current objects by upload age: old packs can still hold recent snapshots'
data. Review any hidden/noncurrent-only lifecycle rule separately before applying it.

## Activation

1. Pass `backup` and `validate` CI, including exact released CRD schemas, native
   restore fixtures and suspend-only CEL. Install the controller through Flux.
2. Render `kubernetes/backup` with strict substitution from both cluster ConfigMaps,
   as CI does, into a private temporary file. Before the first rollout, check that
   any old backup jobs/producers are inactive and their journal has no recorded
   recovery intent. Keep existing stage claims; removed workers and unused namespace
   staging claims must not cause source or backup PVC deletion.
3. For acceptance, apply the rendered support resources **excluding `Schedule`**;
   keep `backup-health.spec.suspend: true`. Verify Operator projections privately,
   read-only sources, stage free space, watchdog readiness, admission type-check
   warnings and live allow/deny behavior. Local CEL tests do not replace server
   validation. Test the exact production API/client and native tool versions.
4. Create a manual `Backup` from each rendered Schedule's `spec.backup`, copying
   its `backend` and `podSecurityContext`, and removing schedule-only fields. Example
   for the private rendered file, without printing credentials:

   ```sh
   yq -o=json 'select(.kind == "Schedule" and .metadata.name == "b2-n8n")' /private/backup.yaml |
     jq '{apiVersion, kind:"Backup", metadata:{name:"accept-n8n",namespace:.metadata.namespace},
          spec:(.spec.backup + {backend:.spec.backend,podSecurityContext:.spec.podSecurityContext} |
                del(.schedule,.concurrentRunsAllowed))}' |
     kubectl create -f -
   ```

   Measure each stopped app from stop request to ready, interrupt one producer and
   one runner, and verify exact recovery and no successful publication of expired
   captures. Hermes/T3 must remain available. Check completion of both the Backup
   and its native Job; inspect sanitized errors only.
5. Restore actual B2 data into isolated storage, following the procedure below.
   Test the existing Hermes Discord route using a uniquely named manual Job from
   the suspended health CronJob with command `python3 /backup/health.py test`.
   Confirm the real message in the existing Discord home channel. Exercise missing,
   unreadable and failed-backup alert paths as well.
6. After acceptance, set Flux `backup.spec.suspend: false` in Git to create the
   Schedules. After their first successful cycle, set the health CronJob's suspend
   to false in Git. Resolve health warnings for every expected app, not just one.

## Restore and incident operation

Use an authorized operator with restic 0.19.1 (included in the pinned K8up image).
Obtain credentials privately from the existing 1Password item; never print them
or pass secret values as arguments. Set `RESTIC_PASSWORD_FILE` and AWS credential
environment variables, `AWS_DEFAULT_REGION=us-west-004`, and the exact app
`RESTIC_REPOSITORY=s3:https://s3.us-west-004.backblazeb2.com/homelab-backup-b2/k8up/<namespace>/<app>`.

List `restic snapshots --tag <app> --json` and verify the corresponding successful
Backup/native Job. For OpenCloud/n8n/Zipline, select `--tag <app>,data`; use the
receipt's `source_snapshot` against the remote snapshot's `original` or `id` if
identifying a particular copy. Restore with `restic restore <id> --target <isolated-dir>`.
Files appear below `source/<claim>` and `recovery`; preserve numeric IDs and xattrs.
For the other apps, use `restic dump <id> /<namespace>-capture.tar > <private-tar>`
and extract with GNU `tar --xattrs --xattrs-include='*' --acls --numeric-owner -xpf`
into a new isolated directory. Never pipe an unchecked/failed dump into a live volume.

Check every restored SQLite DB with `PRAGMA integrity_check`. For PostgreSQL,
restore `roles.sql` with the matching major client before `database.dump`;
handle known bootstrap-role collisions explicitly and use `pg_restore --exit-on-error`.
Immich needs its existing vector extensions. Restore the original 1Password keys
before starting apps; validate n8n credential decryption, OpenCloud file access,
Zipline uploads, Authentik login, and Headscale/gateway identity separately.
These per-app backups do not promise a shared Headscale/gateway snapshot instant.

For Hermes/T3, verify chats, attachments, auth/grants and an unpublished commit,
staged/unstaged changes and tracked deletion. All captured Git content is present;
no custom reconstruction step is required. Restore T3 at `/var/lib/t3` in an
isolated environment, or repair linked-worktree pointers before use. Never attach
restored agents, production OIDC/webhook ingress or a second PGlite instance to
production data. Rebuild excluded caches and GitOps configuration after recovery.

The hourly health Job checks expected Schedule coverage, latest failed/stuck
Backup/Check/Prune, recent successful Backup/native Jobs, and actual B2 snapshot
freshness for all nine apps. It uses only read access to those API resources and
Jobs, with no Secret API reads or source mounts. It alerts after 24 hours without
a valid recent point or when status cannot be read. Delivery uses Hermes's
native `deliver_only` `/webhooks/backup` route and only
`n8n-connect/hermes_webhook_secret`; success requires HTTP 200 plus
`status: delivered` and `target: discord`. Delivered identical warnings are
suppressed for 24 hours; recovery resets suppression. Failed deliveries retry.
There is no independent whole-node-outage alert because both checks and Hermes
run on this node.

For a stuck stopped capture, leave the watchdog running and inspect
`backup-capture-state`, Backup/Job status and app readiness. Recovery retries if
API access fails. Never erase a journal with recorded replicas or suspension
state, delete source/stage claims, force-unlock an active repository, or switch
repositories as a shortcut. Keep original recovery material until a verified
isolated restore succeeds.
