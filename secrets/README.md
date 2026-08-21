# Component secret bundles

Production receives one UTF-8 JSON document per deployment component. GitHub
stores each complete document as one environment secret; workflows write a
mode-0600 temporary file without printing it. There is no repository-wide
field registry or mapping program.

| Component | GitHub environment secret | Authoritative validator |
| --- | --- | --- |
| Apps runtime | `APPS_SECRET_BUNDLE` | `apps/compose/homelab/prepare_release.py` |
| OpenClaw runtime | `OPENCLAW_SECRET_BUNDLE` | `scripts/ci/compose_release_engine.py` |
| PVE access | `PVE_SECRET_BUNDLE` | `infra/ansible/playbooks/reconcile.yml` |
| Tailnet | `TAILNET_SECRET_BUNDLE` | `infra/ansible/playbooks/reconcile.yml` |

Every document has exact `component` and `version: 1` fields. Runtime bundle
schemas are intentionally defined beside their consumers. OpenClaw accepts:

```json
{
  "component": "openclaw",
  "version": 1,
  "gateway_token": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "discord_bot_token": "...",
  "exa_api_key": "..."
}
```

The OpenClaw field set is exact. `gateway_token` is exactly 64 lowercase hex;
the other two values are nonempty single lines. The apps package documents its
own exact nested schema in `apps/compose/homelab/README.md` and validates it by
actually rendering a throwaway release before host installation.

Infrastructure bundles use this envelope:

```json
{"component":"tailnet","version":1,"values":{"tailscale_auth_key":"..."}}
```

The PVE `values` object contains `deploy_ssh_public_keys`; the tailnet `values`
object contains `tailscale_auth_key`. Unknown or missing fields fail the
selected reconciliation before mutation.

The release SSH wrapper validates and atomically installs an apps or OpenClaw
bundle at its fixed root-owned path, then renders only the active runtime slot.
Bundle values and their hashes never enter the release descriptor, state file,
command output, or rollback source. A manual run of the owning runtime workflow
rotates secrets without a repository change; rollback always combines the
selected code release with the current component bundle.

Tailnet OAuth, the deploy SSH key and known-host set, and the private-config
read key are CI connection credentials rather than service configuration. They
remain individually scoped to the jobs that establish those connections.
