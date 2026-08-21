# OpenClaw package and images

OpenClaw keeps one dedicated unprivileged LXC. The host owns only Docker,
firewall/account primitives, durable state, the stable release launcher, and
the current component secret bundle. It does not build images, retain a Git
checkout, or install a target-specific deployer.

`gateway/Dockerfile` and `ctf/Dockerfile` pin every base image by digest and
run their image-local contract checks while building. The two standard Buildx
jobs publish independently, with GHA cache, provenance, SBOM attestations, and
exact `repository@sha256` outputs.

`runtime` is the self-contained Compose package. Its `release.json` names the
Compose file, package smoke test, and `openclaw` versioned secret bundle. The
complete release descriptor contains only the homelab commit, private-config
commit, and the two exact OCI image identities. Archive SHA-256 values are
verified as transport checks, not supplied as promotion coordinates.

The common Compose release engine validates the package and image identities,
stores immutable source by SHA, materializes an inactive runtime slot with the
current `/etc/openclaw/secrets/openclaw.json`, and activates it with Compose
health waiting plus `runtime/smoke.sh`. Atomic state and the previous source
provide rollback and interrupted-deployment recovery. The same engine and
wrapper serve the apps package; OpenClaw-specific readiness remains a package
hook rather than a second deploy state machine.

See `docs/runbooks/openclaw.md` and `docs/runbooks/compose-release.md` for the
operator flow.
