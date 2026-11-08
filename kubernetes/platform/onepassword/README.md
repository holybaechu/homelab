# 1Password Connect, Operator and Injector

This layer uses the official [Connect/Operator and Injector charts](https://github.com/1Password/connect-helm-charts).
Its Flux Kustomization is active after out-of-band bootstrap. MySQL and n8n's
environment use verified Operator replacements; the ESO SDK store still serves
the remaining consumers. Keep ESO running until every
consumer below has moved and its replacement has been verified.

Connect is an internal ClusterIP service at
`http://onepassword-connect.onepassword.svc.cluster.local:8080`; it has no ingress.
The chart's API and sync containers share an ephemeral encrypted cache, which can
be rebuilt from 1Password. Connect has no Kubernetes API token. The Operator and
Injector retain their chart-managed RBAC and Kubernetes API access.

## Secure bootstrap

An administrator with authenticated 1Password access and Kubernetes Secret,
namespace and Flux permissions must perform this outside the agent session.
Use only the replacement credentials: the previously exposed server credentials
were invalidated. Never reuse the uploaded copy.

The replacement server credentials are attached to `Homelab/Talos`; discover the
exact attachment name/ID before fetching it. The infrastructure client token is
`op://Homelab/Talos/vw545gqsrs2exmwzk2ib3qm6ie`; the separate Hermes client token is
`op://Homelab/Hermes Agent/dxraaqrzo4qi6p3swaak5ebyue`. Both references have valid
syntax, but storage does not prove Connect authentication or vault scope. Do not
normalize their IDs or substitute a service-account token.

With an already-authenticated administrative CLI, inspect only nonsecret metadata
(the raw item JSON must remain inside the pipe):

```sh
op item get Talos --vault Homelab --format=json | python3 -c '
import json, sys
item = json.load(sys.stdin)
print(json.dumps({
    "files": [{k: f.get(k) for k in ("id", "name")} for f in item.get("files", [])],
    "fields": [{k: f.get(k) for k in ("id", "label", "type")} for f in item.get("fields", [])]
}, indent=2))'
```

Fetch the verified attachment with `op read --out-file` using its exact reference;
never print it. Fetch the infrastructure token without a trailing newline:

```sh
op read --no-newline --out-file /secure/path/operator-token \
  'op://Homelab/Talos/vw545gqsrs2exmwzk2ib3qm6ie'
```

Use a private directory and mode-0600 files. Existing administrative authentication
is required: a token stored behind an unstarted Connect server cannot bootstrap
that server.

1. Retrieve the saved replacement `1password-credentials.json` attachment into a
   private local directory as above. Verify the replacement integration's vault
   grants and preserve the file securely for recovery. A service-account token is
   not a substitute. Do not put credentials in Git, Helm values or chat.
2. Retrieve the saved infrastructure Connect client token into the mode-0600
   `operator-token` file without a trailing newline. Verify that it is read-only
   and restricted to the infrastructure vault (`Homelab`). Separate app and Hermes
   tokens are required before migrating those consumers: app tokens must not have
   Hermes's all-logins grants.
3. Once the root cluster resources are reconciled, create the two bootstrap
   Secrets from those files using an administrative kubeconfig:

   ```sh
   kubectl -n onepassword create secret generic onepassword-connect-credentials \
     --from-file=1password-credentials.json=/secure/path/1password-credentials.json
   kubectl -n onepassword create secret generic onepassword-operator-token \
     --from-file=token=/secure/path/operator-token
   ```

   These commands do not print the credentials or pass token values as arguments.
   Use raw JSON for the credentials file; Kubernetes performs the base64 encoding.
   Do not create an ExternalSecret for bootstrap: Connect must not depend on ESO
   or on its own Operator to obtain authentication.
4. Set `spec.suspend: false` for `onepassword` in the cluster reconciliation file
   **in Git**. Reconcile the root and then `onepassword`. Verify both HelmReleases,
   the Connect API and sync health, the Operator, and the Injector before labeling
   any application namespace `secrets-injection=enabled`.
5. With a securely supplied client token in `OP_CONNECT_TOKEN`, and no
   `OP_SERVICE_ACCOUNT_TOKEN`, verify the real CLI against Connect from a trusted
   cluster client. Keep JSON item contents and reference values out of output:

   ```sh
   export OP_CONNECT_HOST=http://onepassword-connect.onepassword.svc.cluster.local:8080
   op item get 'Cloudflare DNS' --vault Homelab --format=json >/dev/null
   op read 'op://Homelab/Cloudflare DNS/credential' >/dev/null
   ```

   Verify the **set of accessible vaults** through the Connect API or integration
   administration UI, not just HTTP health. `op vault list` and `op item list`
   are not supported with Connect in CLI 2.41.1. An app token must
   be denied vaults outside its intended scope. Grant the Hermes-only token all
   supported login vaults as authorized; verify actual coverage before claiming
   every account login is available.

The agent's Kubernetes identity cannot create namespaces/Secrets/HelmReleases or
read Secrets/ExternalSecrets. An administrator must reconcile the root Git
configuration to create the namespace, provision the two named bootstrap Secrets,
and let Flux install the releases. Do not grant the agent broad Secret access or
bypass that boundary through temporary pods, exec, RBAC grants or
credential-copying jobs. Metadata inspection of the existing store also requires
an administrator. Verify the newly designated Hermes token independently; never
reuse an old service-account value as a Connect token.

## Application Secret contracts

The existing cluster has 23 ESO targets, 57 field mappings and four templates.
`clusters/homelab/onepassword.yaml` owns all existing source references. Preserve
those item/field IDs and the credentials themselves; do not generate new database,
encryption, signing or OIDC credentials as part of this migration.

The official Operator reads **whole items** and derives Secret keys from field
labels or file names, not field IDs. It cannot select/rename fields, combine
items, execute ESO templates or apply `?ssh-format=openssh`. Before switching a
consumer, verify its real source metadata and resulting keys. Prefer existing
items only when they satisfy the exact contract and do not copy unrelated secrets.
In particular, copying `Hermes Agent` wholesale would copy its broad browser-vault
client token. Do not do this. Any required narrow item/file representation must preserve
its existing source credential and have an explicit rotation procedure; unmanaged
duplicates are not a replacement for the existing source of truth.

| Namespace | Existing target | Contract that must survive cutover |
| --- | --- | --- |
| cert-manager | `cloudflare-dns` | `api-token` for the DNS01 ClusterIssuer; retain controller Secret interface |
| identity | `authentik-env` | Eight exact environment/blueprint keys across Authentik and four OIDC items; chart server and worker share this Secret |
| identity | `authentik-postgresql` | `POSTGRES_PASSWORD`, matching the initialized PostgreSQL database |
| identity | `headscale-oidc` | `client-secret` file referenced by Headscale's config |
| networking | `headscale-gateway` | `authkey`; preserve gateway identity/state and Tailscale entrypoint |
| networking | `adguard-credentials` | Rendered `AdGuardHome.yaml`; strict username/bcrypt validation, JSON quoting, cluster substitutions, UI-edit-preserving initializer and TLS mount |
| homelab | `cloudflare-ddns` | `CLOUDFLARE_API_TOKEN`; Injector candidate only with verified `/bin/ddns` entrypoint and rotation trigger |
| homelab | `zipline-env` | `CORE_SECRET`, `OAUTH_OIDC_CLIENT_SECRET`; explicit Operator keys, unchanged image entrypoint and Reloader policy |
| homelab | `immich` | `DB_PASSWORD` for both server and PostgreSQL, plus JSON-escaped OIDC `immich.json` and all current config fields |
| homelab | `qbittorrent-credentials` | Rendered `managed.conf`; strict username/PBKDF2 validation and JSON quoting; retain merge.py, torrent state and root/s6 startup |
| homelab | `mysql-env` | `username`, `password`, `root-password`; mounted password files and authenticated readiness probe; **no credential-triggered restart** |
| homelab | `langfuse-env` | Eleven exact shared Helm/backend keys; preserve initialized PostgreSQL, ClickHouse, Redis/MinIO credentials, salt and encryption key |
| homelab | `n8n-env` | Encryption key and owner email/hash, including credential-import init container |
| homelab | `n8n-hermes-credentials` | JSON array `credentials.json` combining webhook and Perplexity keys, escaped with `toJson`; preserve IDs/types and import-before-start |
| homelab | `hermes-coder-git` | `github-token`, OpenSSH `signing-key`; preserve Git helper and signing-file modes |
| homelab | `hermes-discord` | Three `DISCORD_*` keys; preserve existing channels/users/session state |
| homelab | `hermes-webhook` | `WEBHOOK_SECRET`, equal to n8n's imported header credential |
| homelab | `hermes-web` | `EXA_API_KEY` |
| homelab | `hermes-browser` | `BROWSER_USE_API_KEY`, separate from browser-vault authentication |
| homelab | `hermes-obsidian` | `OBSIDIAN_AUTH_TOKEN`; Injector candidate for the explicit obsidian-sync shell command only |
| homelab | `hermes-services` | Five keys combined from OpenCloud, Zipline and qBittorrent; preserve MCP credential-file setup |
| homelab | `hermes-langfuse` | Two project keys shared with Langfuse initialization |
| homelab | `hermes-immich` | `IMMICH_API_KEY` for immich-mcp; verify the pinned image entrypoint before considering Injector |

Headscale's config, AdGuard's template and Immich's JSON also contain Flux
substitutions; moving files to 1Password does not make those substitutions happen.
Do not turn the ESO Go templates into literal Operator item fields. Keep safe
format-specific rendering and validation with each owning app rather than adding
a generic replacement templating controller.

## MySQL and n8n replacements

`mysql-connect` and `n8n-connect` are separate Operator-owned Secrets, sourced
from the existing items by their verified vault/item IDs. No credentials or item
labels need changing for these two sources. MySQL and both n8n containers now use
explicit keys from these replacements after administrative byte-parity checks.
`mysql-env` and `n8n-env` remain for rollback, without active consumers;
`hermes-webhook` and all existing ESO templates remain in service.
Do not delete either OnePasswordItem while its Secret is in use;
the Operator finalizer deletes the corresponding Secret.

Before any future cutover or rollback verification, run this read-only check from an administrative checkout with
permission to read the two OnePasswordItems and their Secrets:

```sh
python3 scripts/verify-onepassword-staging.py mysql-connect n8n-connect
```

It compares exact decoded bytes (including newlines), source paths, Ready status,
owner UIDs and allowed key sets, without printing credentials or writing files.
A denied read or any mismatch exits nonzero. Return only the two PASS lines, not
Secret JSON. This check is intentionally unavailable to the agent's restricted
identity; do not grant it Secret access or run the check inside a new workload.

The consumer mappings preserve these contracts:

- MySQL: map `username`, `password`, `root_password` to its existing environment
  and password-file paths (`root-password` remains the projected filename).
  Project only the two password keys; never mount the entire replacement Secret.
  Preserve the initialized database, authenticated readiness and TLS-only Reloader
  policy. Verify the existing account against MySQL; changing 1Password alone
  does not rotate database accounts.
- n8n: map `encryption_key`, `username`, `owner_password_hash` to the existing
  three `N8N_*` variables in **both** the server and credential-import container.
  Do not use `envFrom`; the whole item can also contain its login password and
  webhook key. Keep the import template on ESO until its Perplexity source and
  safe JSON rendering are migrated. The webhook key can later supply the explicit
  `WEBHOOK_SECRET` variable in Hermes, which is migrated last.

The uploaded source metadata covers all 15 source items, 23 ESO targets and 57
mappings, with no missing/ambiguous fields. Null-only files/sections/URL labels
are serialization artifacts: there are no attached files in this export. Metadata
is not proof of credential byte equality or authentication.

## Source labels and staged replacements

Keep the labels now edited in the official app; no further cosmetic renames are
needed. Existing ESO references use field IDs and must remain unchanged. The
[deployed Operator 1.12.1 Secret builder](https://github.com/1Password/onepassword-operator/blob/v1.12.1/pkg/kubernetessecrets/kubernetes_secrets_builder.go)
**preserves case**, trims invalid leading/trailing characters and replaces
internal invalid runs (including spaces) with hyphens. It does not lowercase
labels. The latest labels below are already lowercase hyphen keys, so each
becomes the identical Secret key. Korean-only labels are skipped; the former
mixed Langfuse project labels both collapsed to `Hermes`.

| Source item | Existing field ID → current label and Operator key |
| --- | --- |
| Headscale | `3u3ughywinbmkybgdopzodjnxy` → `oidc-client-secret`; `biwyjxnpkpvmrmy42ffpt45fpe` → `gateway-auth-key` |
| Zipline | `eteoaoehfwqkierraxfgbx5xru` → `core-secret`; `6rvlh45inzbb55zwnyi53spfne` → `oidc-client-secret`; `mq3jr7zyvy5zyff2zzpz7zrmgq` → `hermes-api-token` |
| Langfuse | `postgres_password` → `postgresql-password`; `database_url` → `database-url`; `clickhouse_password` → `clickhouse-password`; `redis_auth` → `redis-auth`; `minio_root_password` → `minio-root-password` |
| Langfuse | `salt` → `salt`; `encryption_key` → `encryption-key`; `nextauth_secret` → `nextauth-secret`; `langfuse_init_project_public_key` → `hermes-project-public-key`; `langfuse_init_project_secret_key` → `hermes-project-secret-key`; `oidc_client_secret` → `oidc-client-secret` |

The reported Zipline reference `op://Homelab/Zipline/hermes-api-token` is a
reference, not a field label or a replacement field ID. The read-only gate must
find the original field ID with label exactly `hermes-api-token`. A literal
`op://...` label fails; do not normalize that mistake into an accepted key.

The OnePasswordItem manifests already reference vault/item IDs, not field keys,
so these edits require no manifest reference changes. Future cutovers must
explicitly project these keys into their existing
interfaces, not use `envFrom` or whole-item mounts. Headscale OIDC now projects
only `oidc-client-secret` from `identity/headscale-connect` to its unchanged
`/var/run/secrets/headscale/client-secret` file and reloads on replacement Secret
changes. Keep `headscale-oidc` on ESO for rollback: restore the old Secret name,
remove the `items` projection and restore its Reloader reference together.
The gateway maps only `networking/headscale-connect`'s `gateway-auth-key` to
`TS_AUTHKEY` and reloads on that Secret. Its PVC/state directory, `TS_AUTH_ONCE`,
node identity, routes, hostname and image entrypoint remain unchanged. Keep
`headscale-gateway` on ESO for rollback: restore its `authkey` reference and
Reloader target together; never delete/reinitialize its persistent state.
Zipline now maps only `zipline-connect` keys `core-secret` and `oidc-client-secret`
to `CORE_SECRET` and `OAUTH_OIDC_CLIENT_SECRET`. Its Hermes API token is not
injected. The existing Reloader auto policy observes the replacement; the image
entrypoint, single-writer strategy, PGlite database and upload PVCs are unchanged.
Keep `zipline-env` on ESO for rollback: restore its original `envFrom` and remove
the two explicit projections together. Langfuse's administrative metadata and
exact source/replacement/ESO byte gate has also passed. Its Web, Worker and four
backends now explicitly select `langfuse-connect` keys through the pinned chart's
Secret interfaces and backend environment mappings. No whole-item mount or
`envFrom` is used; `database-url` and notes remain unprojected. Keep `langfuse-env`
on ESO for rollback, restoring names and keys in both Langfuse manifests together.
The shared `hermes-langfuse` target remains on ESO and Hermes is unchanged.
Six of the 23 target contracts now use Operator replacements; all 23 ESO targets
remain for rollback or active consumers. Operator auto-restart remains off.

### Consolidated remaining-source gate

Consumer-free Operator replacements now also stage Authentik, Immich, AdGuard,
qBittorrent and Obsidian. Identity has separate Zipline, Immich and Langfuse
replacements for Authentik's blueprint inputs; Headscale's replacement already
exists there. No workload references, database credentials, entrypoints, PVCs or
restart policies change during staging. The Hermes bootstrap ConfigMap is
unchanged. No additional target is migrated until its administrative gate passes.

Perform these **five ESO-only source edits in one administrative batch**, using
the official app's in-place label editor and the exact existing field IDs in the
inventory below: Immich's three custom fields, Authentik's email/secret/database
fields, AdGuard and qBittorrent's password-hash fields, and Obsidian's three custom
fields. Preserve IDs, values, types, sections, URLs and history; do not recreate
fields or apply JSON templates. These sources have no live Operator consumers.
Do **not** rename MySQL/n8n yet: those require the separate protected sequence.

After the new manifests reconcile and the Operator observes the edits, run one
read-only gate from the authenticated administrative Windows checkout:

```powershell
git fetch origin main
git show origin/main:scripts/verify-onepassword-staging.py | mise x python@3 -- python - authentik-connect immich-connect adguard-connect qbittorrent-connect obsidian-connect
```

Return only PASS/FAIL/INFO output. Each check continues independently after a
failure; denied/missing reads are failures, never a reason to grant the agent
Secret access. Matching versions with differing bytes still require private
investigation; do not regenerate credentials. If the Operator version is stale,
use the documented Operator-only refresh, then rerun this same batch.

The checks cover these **seven** existing ESO targets, without authorizing an
immediate Hermes restart:

| Check | Exact existing contract compared |
| --- | --- |
| `authentik-connect` | `authentik-postgresql` plus all eight `authentik-env` keys, checking source metadata/bytes and ownership for each of its five source items in `identity` |
| `immich-connect` | `immich` database bytes and the entire parsed JSON config, including the exact OIDC string; also `hermes-immich` API-key bytes |
| `adguard-connect` | Complete `adguard-credentials` file bytes, including validated username/bcrypt hash and cluster substitutions |
| `qbittorrent-connect` | Complete `qbittorrent-credentials` file bytes, including validated username/PBKDF2 hash |
| `obsidian-connect` | `hermes-obsidian` token bytes and all source fields, including unused vault identity/encryption fields |

App-local stdlib renderers are staged separately from the live template
ConfigMaps. They reuse the existing AdGuard/qBittorrent templates, reject invalid
credentials and unknown expressions, preserve JSON quoting, and write private
files. Immich's public template retains every current config field and Flux
substitution; its renderer JSON-escapes the exact UTF-8 OIDC credential without
newline conversion. These are not new secret controllers and do not receive
Connect tokens. The checker does not execute ConfigMap code on the administrator's
machine. AdGuard/qBittorrent comparisons are byte-exact; Immich compares the full
JSON object because safe JSON whitespace/escaping can differ without changing
configuration or credential bytes.

These renderers are **not yet mounted in live workloads**. After PASS, cut over
AdGuard's desired-file input without changing its UI-edit-preserving initializer;
keep qBittorrent's merge.py and root/s6 startup. Immich needs a private emptyDir
rendering init before server startup and explicit database-key mappings in both
server and PostgreSQL. Project only each consumer's required keys. Keep Hermes
last even when its staged Obsidian/Immich comparisons pass.

The reviewed metadata for Authentik, AdGuard and qBittorrent contains one localized
website URL label (`웹사이트`) whose Operator key is empty. The checker permits
only that exact label for those exact items (or no URL), with a nonempty href,
no files and complete source/replacement key/byte equality. It never prints the
URL, accepts no arbitrary skipped labels and does not permit extra Secret keys.
Cloudflare and the token-bearing Hermes source remain blocked as described below;
staging or a seven-contract gate is not full ESO retirement.

### Cloudflare stays on ESO

Cloudflare DNS's built-in `credential` label remains `자격 증명` and is uneditable
in the official app. Operator 1.12.1 skips its empty normalized key. Its
[OnePasswordItem spec](https://github.com/1Password/onepassword-operator/blob/v1.12.1/api/v1/onepassworditem_types.go)
only supports `itemPath`, with no field-ID selection or key remapping. The two
unused `cloudflare-connect` staging resources remain, but neither is a supported
replacement for `cloudflare-dns` or `cloudflare-ddns`. Ready alone is insufficient.
Both targets are excluded from the checker, including its default invocation;
explicit requests fail before any reads.

Do not delete/recreate the built-in field, change category, populate the unset
`validFrom`/`expires` dates, or duplicate the token into an unmanaged field.
The [official Injector](https://developer.1password.com/docs/k8s/injector/) can
resolve `op://Homelab/Cloudflare DNS/credential` by field ID for DDNS, but does not
create cert-manager's required DNS01 Secret. DDNS also needs a verified
`/bin/ddns` command, an exercised item-change restart trigger and credentials
that cannot retrieve the broad Hermes token. A Homelab-scoped Connect token is
**not** safe for DDNS: that vault contains the Hermes broad token, even if the
Connect token itself has no other vault grants. No Injector cutover is configured
here. Keep Cloudflare on ESO until a supported integration with safe scope and
rotation, or an explicitly reviewed minimal source adaptation, satisfies both
consumers.

### Read-only administrative gates

**Automated JSON-template writes remain disabled.** A real Cloudflare preview
populated nonempty DATE values absent from its source. CLI assignments change
values/types, not labels; sparse templates have no verified preservation
contract, and JSON edits can overwrite passkeys invisible in CLI JSON. Never
apply templates or relax preservation checks. `--apply` refuses before any CLI
call, even with `--confirm-no-passkeys`.

App label read-backs are not independent proof that IDs or values survived.
Before cutover, an administrator needs an authenticated `op` session plus read
access to the staged OnePasswordItems, their Secrets and existing ESO Secrets.
Run the gate first; do not restart the Operator merely because a check failed.
On Windows, fetch and run the
reviewed checker without pulling/resetting a dirty checkout:

```powershell
git fetch origin main
git show origin/main:scripts/verify-onepassword-staging.py | mise x python@3 -- python - headscale-oidc headscale-gateway zipline-connect langfuse-connect
```

Require a PASS for **each consumer being cut over**, independently. A failure for
Zipline or Langfuse does not invalidate a Headscale PASS. Only `op item get` and
`kubectl get` are used, plus a captured `op read --no-newline` for Zipline's
existing Hermes field-ID reference. Raw
JSON and credential bytes remain in captured subprocess memory, never output or
files. Each PASS checks:

- Source vault/item/title and exact field IDs, labels, types, purposes and section
  membership against reviewed metadata; no files or unreviewed URLs. The exact
  Operator-skipped Langfuse and localized website labels are described above/below.
- Every current nonempty source field, including Zipline's Hermes token and
  optional notes, against the replacement's exact key set and UTF-8 bytes.
- Operator ownership UID, source path, Ready status, and the existing ESO target's
  exact key set and decoded credential bytes, including newlines.

A mismatch or denied read exits nonzero with sanitized output. The checker
continues through every requested target, and runs source-metadata comparison
independently of the Kubernetes parity result. Each FAIL identifies the stage,
resource namespace/kind/name (or reviewed item ID), and a fixed category:

- `access-denied`, `unauthorized`, `not-found`, or `command-failed exit=N` identifies
  the failing read. Other CLI errors are deliberately not interpreted as auth
  failures. Resolve access/session/existence in that administrator's context;
  do not share raw errors.
- `operator-not-ready Ready=False|Unknown|missing|invalid` identifies readiness,
  without displaying the condition message, which can contain sensitive errors.
- `source-label` identifies the reviewed field ID and expected label; actual
  unreviewed labels are never printed. Other `source-field-*` categories identify
  type, purpose or section drift. Review the existing item in the official app;
  do not recreate fields or use JSON-template writes.
- `replacement-ownership`, `item-path` and `replacement-item-path` require
  investigation of controller ownership/source identity, not a blind restart.
- `replacement-keys`, `replacement-missing-keys`, `eso-keys` and
  `source-replacement-keys` distinguish key-set errors from
  `eso-replacement-bytes` and `source-replacement-bytes`. Only reviewed key names
  are printed; unknown names are counted. A byte failure prints no values or
  digests. Do not regenerate credentials or relax exact-byte checks.

An INFO line compares the source item version with the Operator Secret's
`operator.1password.io/item-version` annotation as `match`, `mismatch` or
`unavailable`. This is a sync hint, not a parity gate: a matching version alone
proves neither correct bytes nor current ESO data. Replacement key/byte failures
with mismatched versions after relabeling justify an Operator-only refresh (or
waiting for its six-hour poll), even when separate source-structure failures
remain. A refresh cannot fix source metadata drift. Have an administrator run:

```sh
kubectl -n onepassword rollout restart deployment/onepassword-connect-operator
kubectl -n onepassword rollout status deployment/onepassword-connect-operator
```

Then rerun all four checks; deployment readiness alone does not prove item sync.
For independent source drift, run this small structural read without Kubernetes
access (it is diagnostic only, never a parity PASS):

```powershell
git show origin/main:scripts/verify-onepassword-staging.py | mise x python@3 -- python - --source-metadata zipline-connect langfuse-connect
```

It reports separate item/vault/title match flags, file/URL/field/section counts,
reviewed field metadata match flags and section membership by anonymous index.
Section IDs are classified as reviewed/unreviewed, section labels as
present/absent; arbitrary names, URLs, values and raw JSON are never printed.
Share only INFO/FAIL lines. Review any extra files or URLs privately rather than
allowing whole-item synchronization to copy unrelated content. A changed section
must not be accepted solely because the field ID/value survived: first verify
that existing ID-based consumer references still resolve to identical bytes.
A copied label-based URI does not validate that older reference. Zipline's
refreshed metadata places its existing Hermes token field in the same reviewed
`add more` section as its other two fields. The checker now requires that exact
membership and compares the **unchanged** field-ID URI from the cluster settings
against replacement bytes with a captured `op read --no-newline`. A missing or
changed reference fails even if metadata and the app's two ESO keys pass.
Do not recreate fields, change values or edit source JSON to satisfy the checker.

Langfuse's reviewed metadata has exactly one empty-label URL with a nonempty
href and an empty normalized Operator key, with no field-key collision. Operator
1.12.1 skips it before copying fields. Only this source may have that single
empty-label URL; nonempty labels (even those normalizing to empty), multiple
URLs, files and any extra replacement key remain blocked. The href is never
displayed or copied by this check. Exact reviewed field metadata and complete
source/replacement/ESO byte parity are still required; matching item versions
alone is insufficient. No source edits or URL deletion are needed.

For future Langfuse revalidation, fetch and run the read-only checker in the
administrator's authenticated Windows checkout, returning only PASS/FAIL/INFO:

```powershell
git fetch origin main
git show origin/main:scripts/verify-onepassword-staging.py | mise x python@3 -- python - langfuse-connect
```

The independent Langfuse check passed before its consumer cutover; rerun it
before any future source-label or key-contract change.

If versions match but ESO bytes differ, inspect item history and ESO freshness
privately before any cutover. Share only PASS/FAIL/INFO lines, not CLI or
Kubernetes JSON. This agent cannot read Secrets or
OnePasswordItems; do not broaden RBAC or bypass it with pods/exec. No arguments
checks all eleven eligible staged checks, including the already-cut-over MySQL
and n8n contracts, which retain their Kubernetes-only comparison. The five new
checks require the consolidated source-label batch above before they can pass.

| Namespace | Eligible staged replacement | Existing ESO target |
| --- | --- | --- |
| identity | `headscale-connect` | `headscale-oidc` |
| networking | `headscale-connect` | `headscale-gateway` |
| homelab | `zipline-connect` | `zipline-env` |
| homelab | `langfuse-connect` | `langfuse-env` |

These gates prove current metadata and byte parity, not historical preservation
of every item property. CLI JSON cannot prove passkey preservation; the metadata
export contains no prior values. Inspect app item history locally for unintended
non-label changes, including attachments/passkeys, and confirm Cloudflare dates
remain unset. Do not claim these were independently compared. Only after each
eligible gate passes should its consumer move, retaining ESO for rollback.
Staging does not retire more ESO targets, prove app authentication or exercise
rotation.

### Standardize all known Homelab source fields

The target convention for **all editable field labels** is lowercase words
separated by hyphens, not snake_case. Scope here is the 15 Homelab source items
in the reviewed metadata export, not the entire personal account. Talos and other
sources outside that export need fresh read-only metadata before adding them;
do not guess their field inventory. Existing built-in `username`, `password`,
`credential`, `type`, `filename`, `expires`, `hostname` and `fingerprint` already
fit the convention where they have those English labels.

The three staged sources above use their latest reported labels. The remaining
inventory and proposed in-place label changes are below; each left-hand token is
an existing **field ID**, not a proposed new field. Do not bulk-rename yet.

| Source | Existing field ID → target label | Coordination |
| --- | --- | --- |
| MySQL Temporary | `ovcwlzwylqyjgsnrpk3amvl36q` → `root-password` | Live Operator consumer: use the protected sequence below |
| n8n | `encryption_key` → `encryption-key`; `hermes_webhook_secret` → `hermes-webhook-secret`; `owner_password_hash` → `owner-password-hash` | Live server and import-init projections; webhook/template consumers retain ID references |
| AdGuard Home | `vu2pnknisjne6k4t5sh5laxf3e` → `password-hash` | Preserve validated bcrypt config rendering and initializer |
| qBittorrent | `t25w4tvxducf5bqt5owlk4mvjy` → `password-hash` | Preserve PBKDF2 config merge and root/s6 startup |
| Immich | `trytrwmlxrksnvjujdd57hqk7a` → `database-password`; `sf4ch3lie57owvqxdwvl3cl6he` → `oidc-client-secret`; `kpjesisize67f6kdidzwdhrriq` → `hermes-api-key` | Preserve DB/server equality, OIDC JSON and MCP interface |
| Obsidian Sync | `auth_token` → `auth-token`; `vault_password` → `vault-password`; `vault_name` → `vault-name` | Preserve vault identity/encryption; do not project unused fields |
| authentik [Admin] | `pinyekye2iau23hchko7js763a` → `secret-key`; `fhw4wuych7l2y5fs62p5leldtm` → `database-password`; `c6t67ly54dysrlik57ft332464` → `email` | Shared chart server/worker/blueprint and database contracts |
| Hermes Agent | `op3m7hudiprbm63fk4wcxqglmi` → `discord-bot-token`; `fn446i6ddovmn7n4jcmck5afg4` → `discord-allowed-users`; `7ooupb36fhrns3ylsnros4fj6e` → `discord-home-channel`; `dhkzzpalgt33qdmvlm6knod72a` → `exa-api-key`; `52qljnwq55ysuf3hb2r4vxawni` → `browser-use-api-key`; `t4554lneydfcucvqsqjuck2zdi` → `perplexity-api-key`; `dxraaqrzo4qi6p3swaak5ebyue` → `onepassword-access-token` | Hermes last; never synchronize this whole item or distribute a token that can read its broad token |
| OpenCloud | `username`, `password` already conform | No custom-field rename needed; preserve Hermes services interface |
| Hermes Coder Signing | `public_key` → `public-key`; `private_key` → `private-key`; `key_type` → `key-type` | Built-ins: only if official app supports label-only edits; preserve OpenSSH transform and key bytes |
| GitHub Hermes | `validFrom` → `valid-from` | Built-in: only if editable without setting a date; otherwise record exception; other English fields already conform |
| Cloudflare DNS | `username` → `username`; `credential` → `credential`; `type` → `type`; `filename` → `filename`; `validFrom` → `valid-from`; `expires` → `expires`; `hostname` → `hostname` | Localized built-ins are blocked unless the official app supports safe in-place label edits; keep dates unset and ESO active |
| Every source | `notesPlain` → `notes-plain` | Built-in: confirm label-only editability; until then retain `notesPlain` as an explicit exception, including in checkers |

Built-in editability has not been independently established except for the
reported uneditable Cloudflare credential. A target label is not permission to
replace a field, change its type/category or round-trip the item through JSON.

Before each batch, capture/review current metadata and app history locally,
including IDs, types, sections, URL labels, files and passkeys. Keep credentials
private. Update that batch's source-metadata checks, allowed key sets and explicit
consumer projections **together**, with exact-byte comparisons against the
retained ESO targets. Keep encryption/database keys, roots/PVCs and application
interfaces unchanged. Check authenticated use and rotation after cutover.

For the already-live MySQL/n8n label changes, avoid a missing-key window:

1. Verify current `mysql-connect`/`n8n-connect` parity against retained ESO targets.
   MySQL's former label-based `root_password` source reference is now its reviewed
   ID `ovcwlzwylqyjgsnrpk3amvl36q`; all other configured field paths in this
   inventory already use actual IDs (including the ASCII underscore IDs).
   Preserve section IDs and signing's `?ssh-format=openssh` query.
2. In a separate reviewed GitOps step, temporarily return MySQL's password-file
   projection to `mysql-env` (`root-password`) and both n8n server/import-init
   references to `n8n-env` (`N8N_ENCRYPTION_KEY`, `N8N_INSTANCE_OWNER_EMAIL`,
   `N8N_INSTANCE_OWNER_PASSWORD_HASH`). Wait for the actual new generations and
   authenticated readiness before editing any live-source label. Preserve MySQL's
   TLS-only restart policy and n8n's import-before-start behavior.
3. Only then edit existing source labels in place. Update checker contracts to
   the new keys, verify source metadata and exact source/replacement/ESO parity,
   and restore Operator consumers with explicit `root-password`, `encryption-key`,
   `username`, `owner-password-hash` projections. Keep webhook/template ID paths.
   Compare the unprojected webhook field too; no secret regeneration or rotation
   is part of this rename. Update owning MySQL/n8n docs in that consumer PR.
4. Verify rollout/authentication and safe rotation, retaining ESO for rollback.
   Do not perform this sequence merely because the four newer staging checks pass.

No MySQL/n8n projection or live source label is changed by this preparatory step.
Their present checker contracts deliberately remain on the current underscore
keys until the protected sequence has reached its metadata/byte gate. Remaining
ESO-only sources can be relabeled in their coordinated batches after ID checks,
then staged and verified before consumer changes; they are not permanent exclusions.

`scripts/relabel-onepassword-sources.py` retains a read-only audit/strict preview
for its eight reviewed editable sources; optional titles limit its scope. It is
not the complete inventory checker and never writes. Pending labels can produce
a preview, not a migration PASS. Use the app for later in-place label edits,
preserving IDs, values, types, sections, URLs, tags, attachments and passkeys;
stop if a built-in label cannot be edited. Manual PowerShell personal-account
sign-in is `Invoke-Expression "$(op signin)"`; the audit rejects Connect overrides
without changing them. Return sanitized diagnostics only, never raw CLI output.

AdGuard, qBittorrent and Immich have staged app-local safe renderers, not live
cutovers. The n8n import template still needs its narrow Perplexity source and
safe JSON rendering; signing still needs the verified OpenSSH transform.
Neither a relabel nor Injector alone resolves those contracts or rotation.

## Cutover, refresh and rollback

- First create **differently named** Operator Secrets beside the ESO-owned targets.
  An administrator must compare decoded key sets and byte values without displaying
  them. Verify the transformed files too, using the original escaping/validation
  rules. Do not run two controllers against the same Secret or rely on implicit
  owner-reference adoption. ESO's `Owner` deletion policy can delete the old Secret.
- Keep existing Reloader policies; Operator `autoRestart` is disabled. Its six-hour
  polling interval matches ESO refresh, but Connect replaces the SDK's one-hour
  cache. Retain MySQL's TLS-only restart policy and its documented coordinated SQL
  password-rotation procedure. Do not enable namespace-wide automatic restarts.
- Injector requires a verified explicit command, Pod-template annotation
  `operator.1password.io/inject`, and a namespace injection label. Pin the injected
  CLI with `operator.1password.io/version: "2.41.1"`. Keep the original command,
  arguments, user and security context. It wraps `op run`; secrets are resolved
  only at startup and are not inherited by unrelated exec sessions. It does not
  restart a workload when a source item changes. **Do not replace a working
  rotation-triggered consumer with Injector until an equivalent item-change
  restart trigger is implemented and exercised.** A bootstrap-token change alone
  does not cover app-item rotation. Reloader only observes Kubernetes resources.
- The Injector creates its fail-closed webhook and TLS certificate at runtime.
  Do not label its own namespace. If it is unavailable, remove the application
  namespace injection label before recovery Pod creation; restore it only after
  verifying the webhook. Do not weaken `failurePolicy` globally.
- Cut over one workload at a time, verify readiness and real authenticated use,
  exercise rotation safely, and keep the old ESO target during the rollback window.
  Retain PVCs, all original database/encryption keys, TLS-controller Secrets and
  Talos-issued credentials. Revert the workload reference to roll back; restore
  the original ESO resource before recreating an old target if it was removed.
- Migrate Hermes **last**, after attached work has completed. Its `Recreate`
  deployment interrupts the running agent. Preserve the root entrypoint, model
  OAuth, profile/session data and existing integrations. Its [browser-vault
  Connect blocker](../../apps/hermes/README.md#1password-logins) must be resolved
  separately; API-key startup mapping is not dynamic login discovery.
- Remove ESO resources/store/chart/repository only after an administrator confirms
  there are no remaining live ExternalSecrets/SecretStores and every consumer
  has verified replacement ownership, auth and rotation. Reloader currently lives
  in `external-secrets`; move its Helm release deliberately or keep that namespace.
  Never delete the namespace merely because ESO has been removed.

For bootstrap rotation, update the existing out-of-band Secrets without printing
values and restart Connect/Operator explicitly; credentials are mounted with
`subPath`, and the Operator token is read from its environment. Reverify real
access before retiring the previous Connect client token or server credentials.
Do not remove the Connect integration or its vault grants while clients depend
on it. Keep ESO available until cutover validation and recovery have succeeded.
