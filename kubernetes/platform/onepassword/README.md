# 1Password Connect, Operator and Injector

This layer uses the official [Connect/Operator and Injector charts](https://github.com/1Password/connect-helm-charts).
Its Flux Kustomization is active after out-of-band bootstrap. It does not replace
the working ESO SDK store or any application Secret yet. Keep ESO running until every
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
| homelab | `zipline-env` | `CORE_SECRET`, `OAUTH_OIDC_CLIENT_SECRET`; Injector candidate only with verified `/zipline/entrypoint` and rotation trigger |
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
