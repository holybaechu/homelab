# Component secret bundles

Production receives one UTF-8 JSON document per deployment component. The apps
document is stored as one GitHub environment secret. Infrastructure jobs render
private controller-side documents from the existing connection inputs; there
is no parallel secret registry or credential-export step.

| Component | GitHub input | Authoritative validator |
| --- | --- | --- |
| Apps runtime | `APPS_SECRET_BUNDLE` | `apps/compose/homelab/prepare_release.py` |
| PVE access | public identity derived from `DEPLOY_SSH_PRIVATE_KEY` | `infra/ansible/playbooks/reconcile.yml` |
| Tailnet | `TAILSCALE_AUTH_KEY` rendered into its versioned bundle | `infra/ansible/playbooks/reconcile.yml` |

Every document has exact `component` and `version: 1` fields. The apps package
documents its nested schema in `apps/compose/homelab/README.md` and validates
it by rendering an isolated release before installation.

Infrastructure bundles use this envelope:

```json
{"component":"tailnet","version":1,"values":{"tailscale_auth_key":"..."}}
```

The PVE `values` object contains `deploy_ssh_public_keys`; the tailnet `values`
object contains `tailscale_auth_key`. Unknown or missing fields fail the
selected reconciliation before mutation. Hosted PVE apply derives its public
key from the same private identity configured for SSH, then verifies that
identity against the generated bundle. Manual controller invocations still
supply a private JSON bundle path as documented in bootstrap.

The release SSH wrapper validates and atomically installs the apps
bundle at its fixed root-owned path, then renders only the active runtime slot.
Bundle values and their hashes never enter the release descriptor, state file,
command output, or rollback source. A manual run of the owning runtime workflow
rotates secrets without a repository change; rollback always combines the
selected code release with the current component bundle.

Tailnet OAuth, the deploy SSH key and known-host set are CI connection credentials rather than service configuration. They
remain individually scoped to the jobs that establish those connections.

## Preparing application password hashes

Existing compatible hashes can be copied from the live AdGuard and qBittorrent
configuration. To replace them, generate the required values on a trusted
controller and store the results only in the private apps bundle:

```sh
read -rsp 'AdGuard password: ' password; echo
htpasswd -bnBC 12 '' "$password" | tr -d ':\n'; echo
unset password

python3 - <<'PY'
import base64, getpass, hashlib, secrets
password = getpass.getpass("qBittorrent password: ").encode()
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac("sha512", password, salt, 100000)
print("@ByteArray(%s:%s)" % (
    base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))
PY
```

The AdGuard command requires Apache's `htpasswd` utility. Keep these values,
component documents, and their backups outside the public repository.
