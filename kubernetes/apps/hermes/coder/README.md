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

External Secrets reads both fields through the shared `onepassword` store. The
key reference requests `?ssh-format=openssh`; the SDK's default is PKCS#8.
Startup copies the root-only Secret files into a memory-backed volume, owned by
UID 10000 with mode `0400`; the gateway mounts this volume read-only. Tokens and
private keys are not copied to the PVC or Git. Profiles share one container and
Unix user, so these mounts are not an isolation boundary between profiles.

For rotation, update the existing 1Password fields and respect the shared refresh
interval and SDK cache. Reloader restarts the Pod after the Secret changes, then
startup recreates the in-memory files. Register a replacement public signing key
before switching its private key, and retain old public keys to verify history.

Verify with `gh api user --jq .login`, `gh repo view holybaechu/homelab`, and a
signed commit in a disposable local repository followed by `git verify-commit
HEAD`. Test Git authentication with a dry-run push. Avoid printing token values,
credential-helper responses, private-key files, or generated Secret contents.
