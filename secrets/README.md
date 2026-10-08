# Secrets

Prepare one UTF-8 JSON bundle per component. Keep bundles, password hashes, and
backups outside the checkout. The examples below contain placeholders.

Use mode `0600` for private controller files. The installed apps bundle must be
a regular, root-owned file at `/etc/homelab/secrets/apps.json` with mode `0600`.

| Component | Hosted input | Validator |
| --- | --- | --- |
| Apps | `APPS_SECRET_BUNDLE` | [prepare_release.py](../apps/compose/homelab/prepare_release.py) |
| PVE | Public key derived from `DEPLOY_SSH_PRIVATE_KEY` | [reconcile.yml](../infra/ansible/playbooks/reconcile.yml) and [deployment-key binding](../scripts/ci/verify_pve_access_bundle.py) |
| Tailnet | `TAILSCALE_AUTH_KEY` | [reconcile.yml](../infra/ansible/playbooks/reconcile.yml) |

Connection credentials and their GitHub environment settings are listed in
[production environment guide](../docs/operations.md#production-environment).

## Apps bundle

Replace every placeholder before use:

```json
{
  "component": "apps",
  "version": 2,
  "authentik": {
    "secret_key": "...",
    "database_password": "...",
    "bootstrap_email": "...",
    "bootstrap_password": "..."
  },
  "headscale": {
    "oidc_client_secret": "..."
  },
  "cloudflare": {
    "traefik_dns_api_token": "...",
    "ddns_api_token": "..."
  },
  "adguard": {
    "username": "admin",
    "password_hash": "$2y$..."
  },
  "qbittorrent": {
    "username": "...",
    "password_hash": "@ByteArray(base64-salt:base64-pbkdf2-digest)"
  },
  "copyparty_users": [
    {"name": "...", "password": "..."}
  ]
}
```

The first Copyparty user has write access to the writable shares. All listed
users can read the shared read-only area.

The preparer rejects unknown keys, the wrong component or version, unsafe account
names, multiline values, malformed hashes, symlinks, and permissions broader
than `0600`. Generated files go into `.secrets/` and `generated/` with mode `0600`.
Both directories are ignored by Git.

The identity stack requires apps bundle version `2`. Generate a random Authentik
secret key of at least 50 characters, and separate random database, bootstrap,
and Headscale OIDC secrets. These values are rendered only into private runtime
files. Identity environment files use Compose's raw format so dollar signs,
quotes, and backslashes remain literal.

The bootstrap email and password initialize `akadmin` only on the first startup;
change an existing user's password in Authentik. Keep the Authentik secret key
stable. Changing the database password in the bundle does not update an existing
PostgreSQL role: change that role's password privately in the database during
maintenance before synchronizing the matching bundle. Take an independent
database backup before either operation.

For existing installations, follow the
[identity deployment procedure](../docs/operations.md#deploy-identity-services)
before replacing a version-1 bundle. The compatible release engine retains the
version-2 bundle and supplies only the old fields when restoring a compatible
version-1 package.

Check a bundle using the
[temporary-copy preparation commands](../apps/compose/homelab/README.md#credentials-and-local-preparation).

## Infrastructure bundles

For a manual PVE apply, supply the public identity corresponding to the
controller's deployment key. Public keys contain only the algorithm and base64
body, without a trailing comment:

```json
{
  "component": "pve",
  "version": 1,
  "values": {
    "deploy_ssh_public_keys": ["ssh-ed25519 BASE64_PUBLIC_KEY"]
  }
}
```

For PVE identity-only reconciliation, the controller generates a version-2
component bundle:

```json
{
  "component": "pve",
  "version": 2,
  "values": {
    "deploy_ssh_public_keys": ["ssh-ed25519 BASE64_PUBLIC_KEY"],
    "proxmox_oidc_client_secret": "..."
  }
}
```

The hosted identity operation derives this client credential from the stable
Authentik key in the apps bundle. No new apps field is needed. See
[identity operations](../docs/operations.md#copyparty-and-proxmox-identity).
The installed PVE identity bundle is private and root-owned at
`/etc/homelab/secrets/pve-identity.json` with mode `0600`.

The tailnet bundle contains its authentication key:

```json
{
  "component": "tailnet",
  "version": 1,
  "values": {
    "tailscale_auth_key": "tskey-auth-..."
  }
}
```

After Headscale is running, use a version-2 tailnet bundle for the gateway:

```json
{
  "component": "tailnet",
  "version": 2,
  "values": {
    "auth_key": "...",
    "login_server": "https://headscale.home.hchu.me"
  }
}
```

Use a Headscale pre-authentication key tagged `tag:gateway`. This is a device
registration key, not a Headscale API key. Follow the
[management migration](../docs/operations.md#move-management-access-to-headscale)
before installing the hosted gateway secret. Version-1 hosted Tailscale bundles
remain supported for bootstrap and recovery.

Unknown or missing fields stop reconciliation before changes are made. Hosted
jobs generate these documents from their connection inputs; manual runs pass
`homelab_secret_bundle` as shown in [setup](../docs/setup.md).

## Generate application password hashes

Compatible hashes can be copied from existing AdGuard and qBittorrent
configuration. To create replacements, run on a trusted controller and save the
output only in the private apps bundle.

AdGuard uses bcrypt. With Apache's `htpasswd` installed:

```sh
htpasswd -nBC 12 '' | tr -d ':\n'
printf '\n'
```

qBittorrent uses a salted PBKDF2-SHA512 hash:

```sh
python3 - <<'PY'
import base64, getpass, hashlib, secrets
password = getpass.getpass("qBittorrent password: ").encode()
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac("sha512", password, salt, 100000)
print("@ByteArray(%s:%s)" % (
    base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))
PY
```

## Rotate credentials

Update the appropriate GitHub environment secret, then follow
[credential rotation](../docs/operations.md#rotate-credentials)
for apps or run `infra.yml` with `unit=tailnet` for a new tailnet key.

The apps SSH wrapper validates and atomically installs the bundle before
recreating the active release. Rollback uses the current bundle. Secret values
and their hashes must stay out of release descriptors, state, and logs.
