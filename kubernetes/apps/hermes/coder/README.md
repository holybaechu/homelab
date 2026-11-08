# Coder development tools

Startup installs checksum-pinned mise and GitHub CLI into the Pod's runtime
volume. Project tools follow the repository's `mise.toml`; after cloning a trusted
repository, run `mise trust` and `mise install` from its root. Downloads, installs,
and shims under `/opt/data/coder-tools` persist on `hermes-data`.

GitHub authentication comes from the `GitHub Hermes` item's `credential` field
in the Homelab 1Password vault. The requested account-wide access uses a classic
PAT with all scopes selected; GitHub still limits it to the account's own
permissions. GitHub CLI reads the mounted token at invocation; the Git
HTTPS credential helper uses the same wrapper. No interactive `gh auth login` is
needed in the Pod.

`Hermes Coder Signing` holds the dedicated Ed25519 key. Register its public key
in GitHub as a **Signing Key**. Git signs commits and tags as
`holybaechu <holybaechu@gmail.com>` and verifies them against the derived public
key. The original Personal key is not used. Treat this as a separate credential
from the token used to push.

The Operator supplies `github-connect/credential` and
`signing-connect/{private-key,public-key}` through explicit root-only projections.
After the administrator's source/replacement/retained-ESO signing-conversion PASS,
startup converts the unencrypted PKCS#8 key to OpenSSH with PyCA cryptography.
It validates the complete private identity and the reviewed public identity,
then reparses the result before writing anything. The pinned Hermes image already
ships cryptography 50.0.2 in `/opt/hermes/.venv`; the first init uses that Python
directly, before tool installation, without network installation or a fallback.
Image upgrades must preserve this dependency and pass the real signing tests.

The memory-backed destination is mode `0700`; its files are owned by UID 10000
with mode `0400`, and the gateway mounts it read-only. Initialization refuses
unsafe source permissions, escaping source symlinks and any nonempty destination;
it never clears or overwrites an existing Git identity. A partial init failure
requires a fresh Pod/emptyDir after fixing the cause, not deletion of PVC data.
Tokens and private keys are not copied to the PVC or Git. Profiles share one
container and Unix user, so these mounts are not an isolation boundary between
profiles. The original `hermes-coder-git` ESO target remains for rollback; revert
the projection, initializer and Reloader references together.

For rotation, update the existing 1Password fields and respect the Operator's
six-hour poll. Reloader watches both replacement Secrets and restarts the Pod, then
startup recreates the in-memory files. Register a replacement public signing key
before switching its private key, and retain old public keys to verify history.

Verify with `gh api user --jq .login`, `gh repo view holybaechu/homelab`, and a
signed commit in a disposable local repository followed by `git verify-commit
HEAD`. Test Git authentication with a dry-run push. Avoid printing token values,
credential-helper responses, private-key files, or generated Secret contents.
