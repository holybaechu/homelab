# Three-host infrastructure

Production has exactly three unprivileged Proxmox LXCs:

- `tailnet` is the management subnet router and exit node;
- `docker_apps` runs the single `homelab` Compose project; and
- `openclaw` runs the isolated Gateway and its session CTF containers.

`ansible/inventory/prod/topology.json` is the one topology document. It owns
the PVE address, VMIDs, host addresses, resources, startup order, features,
devices, mounts, and the explicit deployment unit for each host. Workflow host
targets are read from that document and cannot be overridden independently.

## One targeted infrastructure entrypoint

`ansible/playbooks/reconcile.yml` requires exactly one unit:

```sh
ansible-playbook -i infra/ansible/inventory/prod/topology.json \
  infra/ansible/playbooks/reconcile.yml -e homelab_unit=apps-host
```

The allowed units are `pve`, `tailnet`, `apps-host`, and `openclaw-host`.
There is no all-host phase. Shared Debian changes are applied by intentionally
running the affected units; application deployment never invokes Ansible.

For `pve`, `pve_lxc_reconcile_mode=plan|audit|apply` runs the small `pct`
reconciler. Live PVE configuration is runtime state. Plan prints the canonical
diff, audit exits nonzero on drift, and apply exports each existing `pct
config` before changing it. Missing LXCs may be created, safe fields and disk
growth are idempotent, and destructive/replacement changes require the exact
VMID confirmation. PVE plan/audit need no component secret; apply receives only
the PVE public-key bundle. The remote workflow marks the tailnet VMID as its
active control path and rejects any change that would restart or replace it;
perform such a change from the PVE console or another out-of-band path.

The other units reconcile only their selected Debian/Docker/firewall/storage
primitives. Both Docker hosts use the same Docker Engine role with an explicit
host policy. The stable `/usr/local/libexec/homelab-release` launcher is a host
primitive and changes only through `apps-host` or `openclaw-host` reconciliation.

## Runtime releases

The apps and OpenClaw workflows each construct a complete current release and
call the same fixed-purpose SSH wrapper. The launcher verifies the upload and
embedded engine; the shipped engine owns Compose validation, exact image
verification, pull, health waiting, semantic smoke, atomic state, rollback, and
interrupted-operation recovery. App/runtime changes do not touch PVE, tailnet,
or host configuration.

Apps nonsecret configuration and smoke live in `apps/compose/homelab`.
OpenClaw runtime policy and smoke live in `infra/openclaw/runtime`; its one
descriptor binds the homelab commit, private-config commit, and two OCI
digests. Runtime credentials arrive as one versioned component JSON document
per target and never enter Git or immutable release state.

## Data and recovery

VMID 111 retains only the TUN device needed by tailnet. VMID 110 retains the
shared `/var/lib/homelab` mount at `/srv/homelab`. VMID 118 retains only the
private CTF workspace mount with its unprivileged UID mapping and no TUN
passthrough. OpenClaw remains isolated from both the management and apps hosts.

Compose owns its named network and stable named volumes. Host reconciliation
and the release engine do not delete durable mounts or volumes. Unknown data
is reviewed and backed up before manual removal.

The immutable control-plane recovery reference and reconstruction order are in
`docs/runbooks/recovery.md`. Release details are in
`docs/runbooks/compose-release.md`, and operator workflow contracts are in
`docs/runbooks/github-actions.md`.
