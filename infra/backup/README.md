# External backup infrastructure

Run this root separately from `infra/talos`. OpenTofu owns cloud buckets and
runtime identities; Flux owns Kubernetes capture/restic jobs. Nothing here orders
an OVH project, accepts terms, activates billing, manages Talos or uploads objects.
The official Backblaze and OVH providers are pinned and checksummed in the lockfile.

## Adoption and activation

- B2 imports **only bucket metadata** for private `homelab-backup-b2`, ID
  `8a6f23878ab0beb4a3120e11`. Preserve SSE-B2/AES256, disabled Object Lock and
  Keep All Versions. There is no lifecycle expiry rule. The first real plan must
  show import with **no bucket changes**, not creation/replacement. Stop on any
  discrepancy, including unexpected lifecycle rules, CORS or retention defaults.
- OVH reads existing project `ca114dc8826842a093ba32f8ead79f99` as a data source,
  not a managed/orderable resource. Its observed `project.discovery` plan is not
  an active PAYG project even though `status` is `ok`. Resolve activation on this
  exact project through the authorized account; do not create another project or
  retry a payment/order blindly. The approved `homelab-backup` description can be
  set by the operator after activation; it is not changed here.
- `provision_ovh=false` and `provision_runtime_credentials=false` are staging
  defaults, not teardown switches. Enable the former only after independently
  reading the active project, confirming Singapore `SGP` availability via its
  v2 Object Storage API, and verifying a separate existing Object Storage owner
  user in this project. Supply that ID as `ovh_owner_user_id`. If none exists,
  stop for approval of the owner identity/bootstrap; do not silently create one
  or use the runtime user as owner. Resource preconditions also reject discovery.
- The new `ovh_cloud_storage_object_bucket` uses the v2 API, region `SGP`, AES256,
  disabled versioning and no Object Lock. Its schema has no `force_destroy` or
  public-access switch. Verify private ACLs and the returned endpoint after
  creation, before uploading. Do not fall back to legacy storage resources on an
  API/eligibility error. If the bucket already exists, stop and adopt it using
  the provider's documented `project_id/bucket_id` import (the API returns the
  ID; it can be `SGP_name` or the bare name). Never guess/import object contents.

SGP is a 1-AZ region supporting Infrequent Access. The bucket itself does not
select the write class: restic must use `s3.storage-class=STANDARD_IA` with
`https://s3.sgp.io.cloud.ovh.net`. B2 uses
`https://s3.us-west-004.backblazeb2.com`. Coordinate these with the independent
restic change. Keep independent repository passwords. Review noncurrent/hidden
version costs before a separate policy change; never expire current restic
objects. B2 Keep All Versions can retain hidden versions after restic prune.

## Credentials and least privilege

Provider credentials are **not** runtime bucket credentials. Do not mount provider
credentials, state or a Connect token into restic workloads. No outputs expose
keys here, even as sensitive outputs. Sensitive state attributes still contain
secrets: state and saved plans are AES-GCM encrypted with an independent ephemeral
`state_passphrase` input. Never change Talos's passphrase/key provider/state.

The maintained official `1Password/onepassword` provider (pinned 3.4.1) writes
cloud resource attributes directly into the two approved `onepassword_item`
resources. Their concealed custom fields are `access-key-id`, `secret-access-key`
and `restic-password`; the latter uses a separate provider-native password recipe
for each item. No credential-copy script, browser extraction or secret output is
needed. Never change a recipe or regenerate an initialized repository password.
The OVH OpenStack password is deliberately not copied to the runtime item.

The current agent has **no authorized writer**. The native browser vault is
read/fill-only and Connect bootstrap remains read-only/confined. Leave
`provision_runtime_credentials=false` until the user explicitly authorizes a local
writer and the operator verifies it. A personal CLI login alone does not
authenticate this provider: its supported choices are Connect, service account,
or desktop SDK integration.

1. Preferred local path: the user runs OpenTofu on their own computer with the
   supported 1Password desktop app, enables **Integrate with 1Password SDKs**
   (currently documented as requiring the beta app), sets nonsecret `OP_ACCOUNT`
   to the verified account name/ID and approves the native unlock prompt. Unset
   `OP_CONNECT_HOST`, `OP_CONNECT_TOKEN` and `OP_SERVICE_ACCOUNT_TOKEN` in that
   process to avoid credential-source conflicts. This needs explicit user approval
   but creates no automation token and shares no personal credentials with an AI.
   If desktop integration is unavailable, stop for approval of a separate temporary
   service-account writer using `OP_SERVICE_ACCOUNT_TOKEN`, with only
   `Homelab:read_items,write_items`, a short expiry (for example 1 hour), no vault
   creation or sharing grants. `write_items` includes item deletion as well as
   creation/editing; it cannot be limited to these two item titles. Do not reuse
   or broaden the runtime Connect token. This broader vault-wide authority must
   be acknowledged explicitly. Revoke the temporary writer after verification.
2. Before enabling provisioning, privately verify the exact Homelab vault UUID
   (`onepassword_vault_id`) and exact-title collisions using the user's official
   CLI/client. The titles are `homelab-backup-b2` and `homelab-backup-ovh`.
   If an item already exists, stop: review/import its exact
   `vaults/<vault_id>/items/<item_id>` address and preserve existing field IDs and
   repository password; do not overwrite it with a generated recipe. New items
   are safe only after absence has been verified. Additional provisioner key/item
   titles need approval before creation.
3. Establish independent state-passphrase recovery and provider bootstrap storage
   in 1Password **before** a real plan/import/apply. Inject provider environment
   variables privately using the user's authorized official `op run`/`op read`
   session. No plaintext tfvars, secret arguments, CI deployment credentials,
   shell tracing or `TF_LOG`. The passphrase cannot bootstrap from an item this
   root itself has yet to create. Existing approved authentication is preferable
   to a new standing writer token.
4. After the separately approved local apply, read back the exact item UUIDs
   through the official client/CLI; compare the three fields privately against
   encrypted-state values without emitting values, lengths or digests. Record
   only UUIDs/field labels for Flux's existing
   [Operator contract](../../kubernetes/platform/onepassword/README.md).
   Do not start jobs before field parity and scope checks succeed. If an item
   write fails after cloud key creation, preserve the encrypted state and retry
   only after reading cloud/item state; do not generate a replacement key.

Item fields and cloud credentials remain in **encrypted state and plans**; a
sensitive flag is not encryption. Item resources also use `prevent_destroy`.
Routine no-op plans must not regenerate the custom restic-password field. Review
provider upgrades or external item edits before applying; never round-trip or
adopt unrelated composite items.

| Credential | Environment / rights |
| --- | --- |
| B2 provider | `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`. Read-only adoption plan: `listBuckets`, `readBucketEncryption`, `readBucketRetentions`. Bucket update additionally needs `writeBuckets`, `writeBucketEncryption` (do not grant retention writes for this configuration). Key creation/read needs `writeKeys`, `listKeys`. No direct `deleteBuckets`, `deleteFiles` or `deleteKeys` grants for routine provisioning. **`writeKeys` permits minting arbitrary keys and is effectively account-wide authority**, not bucket-scoped least privilege. Requires explicit approval, a short-lived operator-only bootstrap, and revocation after use. Never claim it is a narrow runtime key. |
| B2 runtime | Only `listBuckets`, `listFiles`, `readFiles`, `writeFiles`, `deleteFiles`, restricted by `bucket_ids` to the imported bucket. No account-wide bucket-name enumeration, bucket administration, retention bypass or key management. |
| OVH provider | `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY`; endpoint fixed to `ovh-ca`. Use the path/method grants below, short-lived and local only. No billing/order/activation/project deletion rights. |
| OVH runtime | Dedicated non-owner `objectstore_operator` user; S3 user policy permits only bucket list/location/multipart enumeration and object get/put/delete/multipart operations on `homelab-backup-ovh`. Explicitly denies `ListAllMyBuckets`, bucket creation/deletion/configuration, object ACL/retention writes and retention bypass. Credential creation depends on policy installation; never distribute its OpenStack password. |

OVH consumer-key rules use **unversioned paths**, including for v2 requests.
Let `P=ca114dc8826842a093ba32f8ead79f99`. Grant only:

| Methods | Paths |
| --- | --- |
| GET | `/auth/details`, `/cloud/project/P`, `/cloud/project/P/role`, `/cloud/project/P/user`, `/cloud/project/P/user/*`, `/publicCloud/project/P/storage/object/bucket`, `/publicCloud/project/P/storage/object/bucket/*` |
| POST | `/cloud/project/P/user`, `/cloud/project/P/user/*/policy`, `/cloud/project/P/user/*/s3Credentials`, `/cloud/project/P/user/*/s3Credentials/*/secret`, `/publicCloud/project/P/storage/object/bucket` |
| PUT | `/publicCloud/project/P/storage/object/bucket/*` |

Replace `P` with the literal project ID when authorizing. For adoption-only
planning, start with GET; once S3 credentials exist, provider refresh also uses
POST on their `/secret` endpoint. Initial user creation sets its role; routine
runs need no password-reset or role-update endpoint. Do not grant DELETE,
`/order/*`, payment, `/services/*`, project creation/upgrade or broad `/*` rights.
After initial creation, narrow wildcard user/bucket paths to the exact returned
IDs where possible. If an expected permission fails, inspect the pinned provider
route and review the exact added grant; never retry with full account access.

OVH evaluates policies before ACLs and falls back to ACLs for missing allows;
an owner has FULL_CONTROL even with an allow-only narrow policy. Therefore the
runtime user must remain a **non-owner**, without other bucket ACL grants. Verify
with real credentials that list/read/write/delete on this bucket work, listing
all buckets and access to an unrelated bucket fail, and bucket administration is
denied before enabling restic. Do not use a failed scope check as a reason to
broaden the policy.

## Execution and review gates

Only local validation is authorized in this change. A real plan/import/apply
requires separately reviewed bootstrap/activation and explicit authorization.
These are operator commands for that later stage, not a CI deployment path:

```sh
umask 077
# BACKUP_STATE and BACKUP_PLAN are private absolute paths outside the checkout.
# TF_DATA_DIR must be dedicated to this root; inject credentials/passphrase privately.
tofu -chdir=infra/backup init -lockfile=readonly -backend-config="path=$BACKUP_STATE"
tofu -chdir=infra/backup plan -out="$BACKUP_PLAN"
# Stream the decrypted plan; never save/publish the JSON (it can contain secrets).
tofu -chdir=infra/backup show -json "$BACKUP_PLAN" | jq -e '
  (.resource_changes | type == "array") and
  all(.resource_changes[];
    (.mode == "data") or
    (.address == "b2_bucket.backup" and .change.actions == ["no-op"]) or
    ((.address == "b2_application_key.runtime[0]" or
      .address == "ovh_cloud_storage_object_bucket.backup[0]" or
      .address == "ovh_cloud_project_user.runtime[0]" or
      .address == "ovh_cloud_project_user_s3_policy.runtime[0]" or
      .address == "ovh_cloud_project_user_s3_credential.runtime[0]" or
      .address == "onepassword_item.runtime[\"b2\"]" or
      .address == "onepassword_item.runtime[\"ovh\"]") and
     (.change.actions == ["create"] or .change.actions == ["no-op"])))
' > /dev/null
```

Use a shell with `set -o pipefail`; a failed producer must fail the review gate.
The allowlist gate rejects every managed delete, replacement, B2 update/create
and unexpected address. It does not grant approval: privately review the real
plan's exact import ID, project, bucket region/owner/private encryption settings,
policies and creations. Defaults permit only B2 adoption; enable each staging
flag only in a fresh explicitly reviewed plan. Apply **only that saved plan**
after authorization (`tofu -chdir=infra/backup apply "$BACKUP_PLAN"`), then read
back exact cloud targets and securely verify secrets before enabling restic.

All managed resources have `prevent_destroy`. Neither bucket provider supports
`force_destroy`; do not invent that argument. `prevent_destroy` is not absolute:
removing a resource block removes its guard, and out-of-band deletion still
works. No DELETE grants are requested. Never use `destroy`, `-replace`, `-target`,
state removal or disabling a staging flag as teardown. Any deletion, key rotation,
Object Lock change or lifecycle cost policy needs a separate recovery-aware plan
review; new Object Lock settings force bucket replacement. Preserve encrypted
state, its backups and the exact passphrase in independent recovery storage; do
not rely on the bucket being managed as the sole state/passphrase backup.

Local/CI validation needs no cloud credentials or backend:

```sh
export TF_DATA_DIR="$(mktemp -d)"
export TF_VAR_state_passphrase="$(openssl rand -hex 32)"
tofu -chdir=infra/backup fmt -check
tofu -chdir=infra/backup init -backend=false -lockfile=readonly
tofu -chdir=infra/backup validate
```

Sources: [1Password provider/authentication](https://developer.1password.com/docs/terraform/),
[1Password service-account scope](https://www.1password.dev/service-accounts/get-started),
[OVH bucket/import schema](https://registry.terraform.io/providers/ovh/ovh/2.23.0/docs/resources/cloud_storage_object_bucket),
[OVH project read](https://registry.terraform.io/providers/ovh/ovh/2.23.0/docs/data-sources/cloud_project),
[OVH policy/ACL evaluation](https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-identity-and-access-management),
[OVH endpoints/classes](https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-location),
[consumer-key rules](https://registry.terraform.io/providers/ovh/ovh/latest/docs/guides/order_consumer_key_access_rules),
[B2 bucket](https://registry.terraform.io/providers/Backblaze/b2/0.14.1/docs/resources/bucket),
[B2 keys](https://registry.terraform.io/providers/Backblaze/b2/0.14.1/docs/resources/application_key),
[B2 capability scope](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities).
