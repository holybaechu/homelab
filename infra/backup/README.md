# External backup infrastructure

Run this root separately from `infra/talos`. OpenTofu owns cloud buckets and
runtime identities; Flux owns Kubernetes capture/restic jobs. Nothing here orders
accounts, accepts terms, activates billing, manages Talos or uploads objects.
The official Backblaze and Scaleway providers are pinned and checksummed in the lockfile.

## Adoption and activation

- B2 imports **only bucket metadata** for private `homelab-backup-b2`, ID
  `8a6f23878ab0beb4a3120e11`. Preserve SSE-B2/AES256, disabled Object Lock and
  Keep All Versions. There is no lifecycle expiry rule. The first real plan must
  show import with **no bucket changes**, not creation/replacement. Stop on any
  discrepancy, including unexpected lifecycle rules, CORS or retention defaults.
- Scaleway signup remains blocked at email verification. No project, bucket or
  key has been created. The unrelated OVH discovery project
  `ca114dc8826842a093ba32f8ead79f99` is **untouched**; this root no longer reads it.
- `provision_scaleway=false` and `provision_runtime_credentials=false` are staging
  defaults, not teardown switches. Before enabling them, obtain account/billing
  verification, explicit provisioning authorization, and approved nonsecret inputs:
  `scaleway_organization_id`, `scaleway_project_name`, `scaleway_bucket_name`,
  `scaleway_region`, `scaleway_runtime_name`, `scaleway_runtime_item_title`.
  No Scaleway name or region is approved by this code; blank defaults fail closed
  when their provisioning gate is enabled.
- OpenTofu owns a dedicated Scaleway project, private bucket/ACL and dedicated IAM
  application/policy/key. The project has `delete_with_resources=false`; the
  bucket has `force_destroy=false`, disabled Object Lock/versioning, and no
  lifecycle expiry. Every managed resource has `prevent_destroy`. If an approved
  project/bucket already exists, stop for explicit adoption review rather than
  creating a substitute. Bucket imports use `region/name@project-uuid`; do not
  import object contents. Preserve existing state before any adoption.
- Verify Standard One Zone availability and pricing in the approved region before
  provisioning. This integration supports `fr-par`, `nl-ams`, `pl-waw`, with
  `https://s3.<region>.scw.cloud` and signing region `<region>`. Provider docs
  contain older region-specific class caveats: independently check the selected
  region, not just its syntax. Runtime rejects a mismatched endpoint/region/bucket.

Standard One Zone is the API write class **`ONEZONE_IA`**, not `STANDARD_IA` or
`STANDARD` (Multi-AZ). Buckets do not select a default write class. Restic 0.19.1
supports `s3.storage-class=ONEZONE_IA` for all writes, including config, keys,
locks, snapshots, indexes and packs; its archive-only metadata exception applies
to GLACIER/DEEP_ARCHIVE, not ONEZONE_IA. The companion runtime passes this option
on every Scaleway invocation, including explicit initialization. Before activation,
check actual object classes and private access with authenticated reads.
B2 stays at `https://s3.us-west-004.backblazeb2.com`; keep independent repository
passwords. Review noncurrent/hidden-version costs separately; never expire current
restic objects. B2 Keep All Versions can retain hidden versions after prune.

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

The approved writer is the user's **Windows-local desktop SDK**, not an agent
service account. The agent has no authorized writer. Connect remains read-only
and unchanged. Leave `provision_runtime_credentials=false` until local SDK
authentication and the remaining provisioning gates are verified. A personal CLI
login alone does not authenticate this provider.

1. Preferred local path: the user runs OpenTofu on their own computer with the
   supported 1Password desktop app, enables **Integrate with 1Password SDKs**
   (currently documented as requiring the beta app), sets nonsecret `OP_ACCOUNT`
   to the verified account name/ID and approves the native unlock prompt. Unset
   `OP_CONNECT_HOST`, `OP_CONNECT_TOKEN` and `OP_SERVICE_ACCOUNT_TOKEN` in that
   process to avoid credential-source conflicts. This creates no automation token
   and shares no personal credentials with an AI. If either local integration is
   unavailable, stop; no service-account fallback or broader Connect token is
   authorized. See the Windows handoff below for the separate CLI and SDK gates.
2. Before enabling provisioning, privately verify the exact Homelab vault UUID
   (`onepassword_vault_id`) and exact-title collisions using the user's official
   CLI/client. The B2 title is `homelab-backup-b2`; the Scaleway title must be
   explicitly approved as `scaleway_runtime_item_title`.
   If an item already exists, stop: review/import its exact
   `vaults/<vault_id>/items/<item_id>` address and preserve existing field IDs and
   repository password; do not overwrite it with a generated recipe. New items
   are safe only after absence has been verified. The separate provisioner/recovery
   title `homelab-backup-infrastructure` is approved in an existing private vault,
   never the Operator-accessible Homelab vault.
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
| Scaleway provider | `SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_ORGANIZATION_ID` for an explicitly approved local bootstrap. Project creation needs organization-scoped `ProjectManager`; application/key and policy creation need `IAMApplicationManager` and `IAMPolicyManager`. Bucket/ACL creation needs `ObjectStorageBucketsWrite` plus read access in the dedicated project. These are consequential management grants, not runtime permissions: privately review exact API requirements, authorize temporarily, revoke afterwards. Never silently widen on failure. |
| Scaleway runtime | Dedicated application, API key default project set explicitly; only `ObjectStorageReadOnly`, `ObjectStorageObjectsWrite`, `ObjectStorageObjectsDelete` on the single dedicated project. No bucket configuration/deletion, project or IAM management grants. |

Scaleway IAM permission sets are **project-scoped, not bucket-only**. Read-only
bucket listing and object read/write/delete extend to all buckets in that project.
Keep the project dedicated to backups; do not place unrelated data there. Review
other application memberships/policies and real multipart/list/read/write/delete
behavior before activation. Verify bucket administration and other-project access
are denied; do not claim listing all buckets inside this project is denied.
Private ACLs are explicit; do not add public bucket policies or ACL grants.

## Windows-local first handoff (read-only)

This checkpoint prepares recovery and an encrypted **adoption plan only**. It
never applies/imports state or creates credentials/items. The launcher suppresses
provider diagnostics and decrypted JSON; it reports only resource addresses and
actions. Windows runtime is **not yet exercised**; stop on failure, retain the
private directory, and report only the failed stage, never raw errors or files.

### One human prerequisite batch

- Use native Windows PowerShell **7.4+** (`pwsh`, not Windows PowerShell 5.1 or
  WSL), Git for Windows, official `op` CLI 2 and OpenTofu **1.13.2**. Verify their
  official downloads/signatures. Use a trusted, non-recorded local terminal: no
  PowerShell transcript, screen sharing, shell tracing or `TF_LOG`/`TF_CLI_ARGS*`.
- In the Windows 1Password **beta** app, enable Windows Hello and Settings >
  Developer > **Integrate with 1Password CLI**, then separately **Integrate with
  1Password SDKs**. Approve the CLI's native prompt for `op whoami`. Later the
  provider has its own SDK/native-unlock prompt, per process (authorization expires
  after 10 minutes idle). CLI success does **not** establish SDK authentication;
  this disabled-runtime plan may not start that provider at all. No remote agent
  can approve either prompt for this laptop.
- Privately verify the account ID (usable by both CLI and SDK), private recovery
  vault UUID and Homelab runtime vault UUID in the app. Check exact titles in
  both vaults. Reuse an existing `homelab-backup-infrastructure` only after
  verifying its identity/fields; stop on duplicates. If either runtime title
  exists, stop for adoption review; do not create runtime items manually.
- In the private vault, use the app to save `homelab-backup-infrastructure` with
  these **concealed, top-level custom fields** (no section). Secret values stay
  on the laptop/in 1Password, not in commands, chat or `.tfvars`:

  | Field label | Initial value / authority |
  | --- | --- |
  | `state-passphrase` | Newly generated independent random password, at least 32 characters; not a Talos/restic password. Save, lock/unlock, and verify recovery privately before planning. Keep independent secure recovery access, not solely this laptop or either managed bucket. |
  | `b2-application-key-id`, `b2-application-key` | Read-only adoption key: only `listBuckets`, `readBucketEncryption`, `readBucketRetentions`, restricted to the existing bucket where supported. No `writeKeys` for this checkpoint. |

  If a credential has not been securely issued/saved, stop before the launch step;
  do not invent placeholders as values or send secrets for an agent to copy.
  Record nonsecret issuance/expiry metadata in that private item. Later bootstrap
  may temporarily replace the B2 provider fields with the already approved,
  short-lived `writeKeys`/`listKeys` authority; it is effectively account-wide,
  must be revoked after bootstrap, and is **not needed now**. Runtime keys and
  repository passwords are written directly by the provider, never copied by hand.

### Prepare isolated sources and private references

Run in a **fresh local `pwsh -NoProfile`**. Substitute only nonsecret placeholders.
Choose an existing local checkout and the reviewed PR338 handoff commit SHA.
Never stash, reset or clean that checkout. The detached worktree uses committed
sources, not dirty files; stop rather than overwrite an existing destination.

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Repo = 'C:\path\to\homelab'
$Revision = '<reviewed-PR338-handoff-commit-SHA>'
$env:OP_ACCOUNT = '<verified-account-ID>'
$RecoveryVault = '<private-vault-UUID>'
$RecoveryItem = '<verified-homelab-backup-infrastructure-item-UUID>'
foreach ($name in @('OP_CONNECT_HOST','OP_CONNECT_TOKEN','OP_SERVICE_ACCOUNT_TOKEN')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
op whoami 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw 'CLI app authentication failed; stop.' }
# Resolve locally after fetching the reviewed PR if necessary; never use dirty HEAD.
$Commit = git -C $Repo rev-parse --verify "$Revision^{commit}" 2>$null
if ($LASTEXITCODE -ne 0 -or $Commit -ne $Revision) { throw 'Reviewed commit unavailable; stop.' }
$Existing = @(Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory -Filter 'homelab-backup-*')
if ($Existing.Count) { throw 'Prior backup handoff found; locate/recover it before starting again.' }
$Base = Join-Path $env:LOCALAPPDATA ('homelab-backup-' + [guid]::NewGuid())
$null = New-Item -ItemType Directory -Path $Base
# Set a protected DACL before creating ANY credential references or artifacts.
$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $Base -AclObject $acl
$Private = Join-Path $Base 'private'
$Source = Join-Path $Base 'source'
$null = New-Item -ItemType Directory -Path $Private
# Never rerun against an existing state: this is first adoption only.
git -C $Repo worktree add --detach $Source $Commit 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Isolated worktree failed; stop.' }
$Fields = [ordered]@{
    TF_VAR_state_passphrase = 'state-passphrase'
    B2_APPLICATION_KEY_ID = 'b2-application-key-id'
    B2_APPLICATION_KEY = 'b2-application-key'
}
$References = foreach ($entry in $Fields.GetEnumerator()) {
    '{0}=op://{1}/{2}/{3}' -f $entry.Key, $RecoveryVault, $RecoveryItem, $entry.Value
}
$EnvFile = Join-Path $Private 'references.env'
$References | Set-Content -LiteralPath $EnvFile -Encoding utf8NoBOM
```

The `.env` contains **references only**, outside Git. The fresh ACL permits only
this Windows user (administrators can still take ownership); use a local NTFS,
BitLocker-protected location, not OneDrive/a shared drive. Both encrypted state
(`backup.tfstate`, including future backups) and plan (`adoption.tfplan`), plus
provider data, stay in `$Private`, never the checkout. Preserve this directory and
its nonsecret source SHA/path for recovery. Before any rerun, locate existing
state/plan and inspect the failure stage; never silently start with fresh state.

### Run only after credential storage and read-only planning are authorized

```powershell
op run --env-file $EnvFile -- pwsh -NoProfile -File `
    (Join-Path $Source 'infra\backup\Review-Adoption.ps1') -PrivateDirectory $Private
if ($LASTEXITCODE -ne 0) { throw 'Adoption review failed; stop. No apply authorized.' }
```

`op run` resolves references only in its child process. Do not run `op read` to
screen, dump item JSON/provider environments, use `--no-masking`, save decrypted
plan JSON, or upload the encrypted artifacts to chat/CI. The launcher captures
`tofu show` before parsing and checks its native exit code, so a failed producer
cannot pass the review gate. Expected: only `b2_bucket.backup: no-op` with the
exact import ID, and no data resources. Scaleway credentials are not required for
this disabled-provider adoption checkpoint; do not inject dummy credentials. A
success **does not** prove SDK write access, an active Scaleway account, or bucket/runtime scope.

**Stop here.** No B2 key is created and no runtime flag is enabled. Scaleway
account/email verification, region/names, dedicated-project scope and temporary
bootstrap authorization remain gates. A later reviewed provisioning plan must
verify SDK authentication, Homelab vault UUID/title absence, expanded provider
grants, exact plan settings and authorization **before** applying only that saved
plan. Verify direct item writes/runtime scope and revoke temporary bootstrap keys.
Leave the read-only Connect/Operator contract unchanged.

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
    (.address == "b2_bucket.backup" and .change.actions == ["no-op"]) or
    ((.address == "b2_application_key.runtime[0]" or
      .address == "scaleway_account_project.backup[0]" or
      .address == "scaleway_object_bucket.backup[0]" or
      .address == "scaleway_object_bucket_acl.backup[0]" or
      .address == "scaleway_iam_application.runtime[0]" or
      .address == "scaleway_iam_policy.runtime[0]" or
      .address == "scaleway_iam_api_key.runtime[0]" or
      .address == "onepassword_item.runtime[\"b2\"]" or
      .address == "onepassword_item.runtime[\"scaleway\"]") and
     (.change.actions == ["create"] or .change.actions == ["no-op"])))
' > /dev/null
```

Use a shell with `set -o pipefail`; a failed producer must fail the review gate.
The allowlist gate rejects every managed delete, replacement, B2 update/create
and unexpected address. It does not grant approval: privately review the real
plan's exact import ID, project, bucket region/private ACL settings,
policies and creations. Defaults permit only B2 adoption; enable each staging
flag only in a fresh explicitly reviewed plan. Apply **only that saved plan**
after authorization (`tofu -chdir=infra/backup apply "$BACKUP_PLAN"`), then read
back exact cloud targets and securely verify secrets before enabling restic.

All managed resources have `prevent_destroy`. Scaleway explicitly sets
`force_destroy=false` and `delete_with_resources=false`. `prevent_destroy` is not absolute:
removing a resource block removes its guard, and out-of-band deletion still
works. Management permission sets can include deletion authority; never treat lifecycle
guards as API authorization. Never use `destroy`, `-replace`, `-target`,
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
[CLI app integration](https://developer.1password.com/docs/cli/app-integration/),
[CLI environment injection](https://developer.1password.com/docs/cli/secrets-environment-variables/),
[SDK desktop authorization](https://developer.1password.com/docs/sdks/desktop-app-integrations/),
[1Password service-account scope](https://www.1password.dev/service-accounts/get-started),
[Scaleway bucket/import schema](https://registry.terraform.io/providers/scaleway/scaleway/2.91.0/docs/resources/object_bucket),
[Scaleway provider releases](https://github.com/scaleway/terraform-provider-scaleway/releases),
[Scaleway permission sets](https://www.scaleway.com/en/docs/iam/reference-content/permission-sets/),
[Scaleway classes/endpoints](https://www.scaleway.com/en/docs/object-storage/concepts/),
[Restic 0.19.1 S3 write handling](https://github.com/restic/restic/blob/v0.19.1/internal/backend/s3/s3.go),
[B2 bucket](https://registry.terraform.io/providers/Backblaze/b2/0.14.1/docs/resources/bucket),
[B2 keys](https://registry.terraform.io/providers/Backblaze/b2/0.14.1/docs/resources/application_key),
[B2 capability scope](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities).
