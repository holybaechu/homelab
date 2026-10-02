# Two-host infrastructure

Production has two unprivileged Proxmox LXCs: `tailnet` provides management
routing and exit-node access; `docker_apps` runs the `homelab` Compose project.

`ansible/inventory/prod/topology.json` owns host addresses, VMIDs, resources,
startup order, devices, mounts, and unit selection. Ansible owns provisioning
and guest configuration. The Docker policy lives beside the apps inventory
host. Start new installations with [bootstrap](../docs/runbooks/bootstrap.md).

## Targeted reconciliation

Run `ansible/playbooks/reconcile.yml` with exactly one of `pve`, `tailnet`, or
`apps-host`. There is no implicit whole-homelab reconcile.

```sh
export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
ansible-playbook -i infra/ansible/inventory/prod/topology.json \
  infra/ansible/playbooks/reconcile.yml -e homelab_unit=apps-host
```

PVE uses `pve_lxc_reconcile_mode=plan|audit|apply`. Plan reports the live diff;
audit fails on drift; apply exports existing configuration before mutation.
Missing LXCs may be created, safe changes and disk growth are idempotent, and
destructive or replacement changes require exact VMID confirmation. The hosted
lane also rejects changes that would restart or replace its tailnet control
path. Run those changes from a trusted out-of-band controller.

PVE plan/audit need no component secret. Apply receives only the PVE access
bundle; tailnet receives only its own bundle. The apps host establishes OS,
Docker, access, durable directories, the PVE trust certificate, and the stable
release launcher. Application activation belongs to the runtime lane.

## Runtime and durable state

`apps/compose/homelab` is the complete application release package. The apps
workflow sends it and the versioned component bundle through the fixed-purpose
SSH transport. The embedded engine owns exact image verification, health,
semantic smoke, atomic state, rollback and interrupted-operation recovery.

Tailnet retains the TUN device. The apps LXC retains the declared
`/var/lib/homelab` bind mount at `/srv/homelab`. Compose owns its named network
and stable data volumes. Reconciliation and deployment retain durable data.

See [release operations](../docs/runbooks/compose-release.md),
[workflow contracts](../docs/runbooks/github-actions.md), and
[recovery](../docs/runbooks/recovery.md).

PVE plan/audit/apply report root-storage headroom. An explicit disabled
`host-managed=0` network option is normalized as unset; enabling host management
still counts as connectivity-affecting drift and preserves the control-path guard.
