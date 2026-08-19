# Merged homelab Compose project

This is the only application stack. The routine direct deployer operates only
this project and contains no legacy-stack branch. Ansible prepares inputs but
never activates application containers.

## Runtime inputs

Ansible renders the release input at
`/etc/homelab/runtime/homelab/.env`. It contains only the nonsecret keys shown
in `.env.example`. Root provisions these service-specific files separately:

- `/etc/homelab/secrets/traefik.env` contains only Traefik's
  `CF_DNS_API_TOKEN` credential.
- `/etc/homelab/secrets/cloudflare-ddns.env` contains only the updater's
  `CLOUDFLARE_API_TOKEN` credential.

Both secret files are root-owned and mode `0600`. The private runtime overlay
also contains:

- `/etc/homelab/runtime/homelab/files/adguard/AdGuardHome.yaml`
- `/etc/homelab/runtime/homelab/files/copyparty.conf`

The direct deployer copies those inputs into an immutable staged release.
It separately injects a release-scoped `.homelab/artifacts.env` containing
only `T3CODE_IMAGE_REF`. The value comes from trusted deployment state and is
validated as `ghcr.io/holybaechu/homelab-t3code@sha256:<64hex>` before Compose
configuration, pull, or activation. It is never part of Ansible runtime
configuration.

## Deployment and recovery

- `platform_traefik_data` and `platform_adguard_work` are declared external so
  they must already exist before the first merged deployment. This preserves
  certificates and AdGuard work data without copying Docker volumes.
- The fixed project name changes container identities. Monitoring and backup
  selectors that use old project/container labels need an explicit cutover.
- CI builds `apps/images/t3code/Dockerfile`, publishes a source-addressed
  staging tag, and records the digest emitted by that same Buildx operation.
  The production release contains neither the Dockerfile nor a build context.
  Activation uses `--no-build` and the exact recorded digest. An unrelated
  application release reuses the last health-approved digest; the first
  release fails closed until its source SHA supplies an exact digest approval.
- The source-only recovery tool records the already-tested split-stack cutover
  procedure. It is never shipped in normal release archives or consulted by
  routine deployment. A reconstructed host uses the ordinary targeted
  bootstrap followed by the same exact-SHA initial activation.
- Repository-wide Compose validation must use Compose's no-env-resolution mode
  until the required absolute secret files exist on the validation host; the
  service env files deliberately remain required in production.
- Routine rollback restores the previous homelab release as one unit, including
  the exact T3 digest recorded with that release.
