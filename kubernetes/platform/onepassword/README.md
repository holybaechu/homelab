# 1Password Connect, Operator and Injector

This layer uses the official [Connect/Operator and Injector charts](https://github.com/1Password/connect-helm-charts).
All 23 former ESO consumer contracts use differently named Operator-owned Secrets.
`kubernetes/clusters/homelab/onepassword.yaml` owns source item paths; app manifests
own explicit key projections. Keep Reloader and its `external-secrets` namespace.
Do not restore ESO for routine refresh or adopt its former Secret names.

Connect is an internal ClusterIP service at
`http://onepassword-connect.onepassword.svc.cluster.local:8080`, without ingress.
Its API and sync containers share an ephemeral encrypted cache that can be
rebuilt from 1Password. Connect has no Kubernetes API token. The Operator and
Injector retain their chart-managed RBAC and Kubernetes API access.

## Secure bootstrap

Bootstrap requires an administrator's authenticated 1Password session and
Kubernetes namespace/Secret permissions. The agent cannot read Secrets,
OnePasswordItems, ExternalSecrets or ClusterIssuers; do not broaden RBAC or bypass
it using pods, exec or credential-copying jobs. Even metadata-only Secret output
requires Secret-read permission.

1. Retrieve the replacement Connect server credentials attached to `Homelab/Talos`.
   Discover the exact attachment name/ID privately, then use `op read --out-file`
   for that verified attachment. Preserve it securely for recovery. Previously
   exposed server credentials were invalidated and must never be reused.
2. Retrieve the read-only, Homelab-scoped infrastructure client token without a
   trailing newline into a private mode-0600 file:

   ```sh
   op read --no-newline --out-file /secure/path/operator-token \
     'op://Homelab/Talos/vw545gqsrs2exmwzk2ib3qm6ie'
   ```

   Server credentials JSON, Connect client tokens and service-account tokens are
   different credentials. A token behind an unstarted Connect server cannot
   bootstrap that server. Keep values out of chat, Git, arguments and logs.
3. Reconcile the root resources to create namespace `onepassword`, then provision
   the independent bootstrap Secrets from the private files:

   ```sh
   kubectl -n onepassword create secret generic onepassword-connect-credentials \
     --from-file=1password-credentials.json=/secure/path/1password-credentials.json
   kubectl -n onepassword create secret generic onepassword-operator-token \
     --from-file=token=/secure/path/operator-token
   ```

   Use raw JSON; Kubernetes performs base64 encoding. Do not bootstrap through
   ESO or the Operator's own OnePasswordItems. These are initial-create commands,
   not overwrite/recovery commands for existing Secrets.
4. Keep the `onepassword` Flux Kustomization active in Git. Reconcile `homelab`
   then `onepassword` in `flux-system`. Verify both HelmReleases, all container
   readiness and Connect's API **and dependency** health. HTTP 200 or Ready Pods
   alone do not prove upstream synchronization or bearer-token authentication.
5. With a securely supplied client token in `OP_CONNECT_TOKEN`, no
   `OP_SERVICE_ACCOUNT_TOKEN`, and the internal `OP_CONNECT_HOST`, verify the
   exact intended item using `op item get --format=json` and the intended field
   with `op read`, capturing/discarding output privately. Verify the complete
   accessible-vault set through the integration administration UI or Connect API.
   CLI 2.41.1 does not support `op item list` or `op vault list` with Connect.

Do not distribute the infrastructure token to applications. A vault-scoped token
can retrieve broader tokens stored in its readable vault, so a nominal app token
is not a least-privilege solution to token-bearing source items. Applications use
explicit Operator Secret projections and need no Connect token.

## Application source and key contracts

The [pinned Operator 1.12.1 Secret builder](https://github.com/1Password/onepassword-operator/blob/v1.12.1/pkg/kubernetessecrets/kubernetes_secrets_builder.go)
copies whole items, not selected field IDs. It preserves ASCII case, trims
leading/trailing invalid characters and replaces internal invalid runs with
hyphens; it does not lowercase labels. Korean-only labels produce no key, and
mixed-language labels can collapse to the same ASCII key. Editable source labels
use lowercase English words separated by hyphens, not snake_case; keep the exact
keys selected by existing consumers. Built-ins such as `notesPlain` are explicit
exceptions, not permission to recreate fields. It also considers files and URL
labels; meaningful URL keys or label collisions can change Secret contents.
Keep existing labels, field IDs, types,
sections, attachments, dates and passkeys intact. Do not use item JSON round-trip
edits or Connect field PATCH to relabel: real preservation checks failed.

Consumers select only required keys, never whole-item `envFrom` or broad-token
mounts. Do not delete a live OnePasswordItem: its finalizer deletes its Secret.
Owner references from this pinned Operator omit the controller bit; verify the
correct group/kind/name/UID and absence of deletion, not `controller=true`.
Ready conditions do not supply `observedGeneration`; do not invent that gate.

| Consumer | Current contract and safeguards |
| --- | --- |
| cert-manager and DDNS | Each namespace's `cloudflare-connect/credential`; preserve ACME account, DNS solver, TLS output interface and DDNS entrypoint |
| MySQL | `mysql-connect`: `username`, `password`, `root-password`; password-file mappings, initialized database and authenticated TLS readiness unchanged |
| n8n | `n8n-connect`: `encryption-key`, `username`, `owner-password-hash`, `hermes-webhook-secret`; import also selects Runtime's `perplexity-api-key` |
| Authentik | Five identity-scoped Operator items explicitly supply all eight environment/blueprint keys; PostgreSQL uses `authentik-connect/database-password` |
| Headscale | Identity selects `oidc-client-secret`; networking gateway selects `gateway-auth-key`; preserve gateway PVC, node identity and `TS_AUTH_ONCE` |
| Zipline | `core-secret`, `oidc-client-secret`; preserve PGlite/upload data and single-writer startup |
| Langfuse | Explicit shared/backend key interfaces; preserve all database, salt and encryption identities; see [operations](../../apps/langfuse/README.md) |
| Immich | Server/PostgreSQL share `database-password`; private init renders complete JSON with `oidc-client-secret`; Hermes sidecar selects `hermes-api-key` |
| AdGuard and qBittorrent | App-local renderers validate username/hash and escape output before unchanged UI-preserving initialization/merge; preserve root/s6 startup and storage |
| Obsidian | Hermes sidecar selects only `obsidian-connect/auth-token`; vault identity and encryption material remain unchanged |
| Hermes runtime | `hermes-runtime-connect` selects Discord, Exa and Browser Use keys; n8n selects Perplexity independently |
| Hermes services | Explicit OpenCloud, Zipline and qBittorrent projections retain the five existing MCP credential-file inputs |
| Hermes Git | Root-only GitHub/signing projections and image-provided PyCA conversion; see [signing and recovery](../../apps/hermes/coder/README.md) |

Keep active app-local renderers, signing conversion and initialization code. n8n's
Node renderer runs before the existing import, rejects invalid UTF-8/empty inputs,
JSON-escapes exact values and writes mode-0600 output on memory-backed storage.
AdGuard/qBittorrent validate hashes and reject unknown template expressions;
Immich preserves the complete configuration. These are runtime contracts, not
one-time migration tools.

### Authoritative narrow items

`Hermes Runtime` (`rvmf543rf6sefjrqiu7csfa2ra`) is the authority for its six
fields (three Discord fields, Exa, Browser Use and Perplexity), identified by
`HERMES_RUNTIME_CONNECT_ITEM_PATH`.
Update only those current fields. The original `Hermes Agent` copies/history are
recovery material, not independently managed credentials. Never synchronize that
original whole item: it contains a broader browser-vault token.

`Cloudflare DNS Runtime` (`34qgxb73mblpjwblihxkmu6uam`), selected by
`CLOUDFLARE_CONNECT_ITEM_PATH`, is the sole
Cloudflare rotation authority. It contains only the credential and empty notes.
The original Cloudflare item was privately reviewed and restored after the DATE
preservation incident; keep it frozen, do not relabel/PATCH it or materialize
absent dates. Coordinate actual provider rotation with both current namespace
projections, DNS01 and DDNS authentication; do not rotate the frozen original.

## Refresh, rotation and upgrades

Operator `autoRestart` stays off. Six-hour polling updates Secret data; existing
per-app Reloader policies determine restarts. Do not enable global automatic
restarts. MySQL restarts only for TLS changes; changing 1Password alone does not
rotate initialized database accounts. Follow [MySQL rotation](../../apps/mysql/README.md).
Other database/encryption/bootstrap-only credentials likewise require coordinated
application changes, not Secret updates alone.

For stale data, first privately check source identity, required keys, owner UID,
Ready state and exact source/replacement bytes. Item-version annotations are sync
hints, not parity proof. If stale, wait for polling or administratively restart
only `deployment/onepassword-connect-operator -n onepassword`, wait for rollout,
then verify actual synchronization. A restart does not fix metadata/key drift.
Never log values, raw JSON, exception bodies, fingerprints or credential digests.

Reloader observes Kubernetes changes, not unsynchronized 1Password edits.
Metadata-only propagation can prove polling without rotating production credentials;
healthy startup is not evidence of interactive login, production rotation or
end-to-end ingestion. Verify the affected app's real authenticated operation.
Hermes uses `Recreate`: schedule credential-triggered restarts after attached work
finishes and preserve model OAuth, profiles, sessions and PVC contents.

For bootstrap rotation, securely update only the relevant out-of-band Secrets,
restart Connect/Operator explicitly, and reverify real authentication/vault scope
before retiring previous credentials. Server credentials use `subPath`; the
Operator token is loaded from its environment. Do not remove an integration or
vault grants while clients depend on them.

Chart/image upgrades must preserve native schemas, post-renderers, security and
explicit projections. Hermes's signing initializer requires image-provided
cryptography 50.0.2 before tool installation; retain real signing tests and avoid
startup network installation or fallback conversion.

## Injector and native browser limitations

Injector requires namespace opt-in, an explicit verified command and
`operator.1password.io/inject`; pin `operator.1password.io/version: "2.41.1"`.
It wraps `op run` at startup, does not update unrelated exec environments or
restart on item changes. Do not replace a rotation-aware consumer until an
equivalent refresh trigger is exercised. Its fail-closed webhook/TLS are created
at runtime. Never label its own namespace. If unavailable, remove only affected
application namespace opt-in before recovery Pod creation; restore after webhook
recovery, without globally weakening `failurePolicy`.

Hermes native browser authentication/listing/OTP is **not Connect-compatible** in
the pinned backend. Startup API-key resolution is a separate feature, not dynamic
login discovery. Do not claim browser integration complete or add a service-account
fallback, secret-printing shim, lock bypass or weaker redaction. A future supported
Hermes-only token needs independent out-of-band bootstrap and verified login-vault
scope; never source it through its own Connect dependency. Stored token references
do not prove active use. Privately identify all clients before moving/revoking
any broad token; deleting its field is not revocation.

## Recovery

ESO retirement is complete: both API groups and the obsolete chart source and
bootstrap Secret are absent. The administrator confirmed the ESO-only token was
exclusive and revoked. Do not repeat CRD deletion, token revocation or accepted
migration gates. One-time tools remain available in Git history at
`11a635b0731548112b99959ba52f4ba416d16b26`, not as maintained operational tools.

For routine recovery, restore the independent Kubernetes bootstrap Secrets from
securely retained Connect server credentials and its scoped client token, then
reconcile Connect/Operator and verify actual authentication and item ownership.
Never use a OnePasswordItem to recover its own bootstrap.

Disaster recovery must preserve exact database/encryption/signing identities,
composite inputs, PVCs, production TLS and Talos credentials. Existing encrypted
recovery and Git commit `6f43609833f03d6598e6adf17211a4c886bbe70e` retain the former
source references/templates if historical reconstruction is explicitly needed;
an old Secret name alone is not a rollback. Do not recreate credentials, rely on
implicit adoption, export redundant plaintext backups or delete original item
history. This repository does not configure automatic MySQL/Langfuse data backups;
follow the owning storage/recovery guides.

Root Flux deliberately has `prune: false` to protect shared resources/namespaces.
Removing a root-owned resource from Git is not proof of live deletion. Do not
enable root-wide pruning to remove an orphan. Keep `external-secrets` and its
Reloader release, Stakater source and RBAC; the namespace name is not an ESO
controller dependency.
