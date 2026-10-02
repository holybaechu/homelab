# Infrastructure

Ansible provisions and configures the two production LXCs. Host identities,
addresses, VMIDs, resources, devices, and mounts belong in
[ansible/inventory/prod/topology.json](ansible/inventory/prod/topology.json).

Use [setup](../docs/setup.md) for controller setup, provisioning,
SSH trust, and the first host configuration.

## Choose one unit

Each run of [reconcile.yml](ansible/playbooks/reconcile.yml) requires exactly
one unit.

| Unit | What it manages | Component bundle |
| --- | --- | --- |
| `pve` | LXC definitions, shared storage, and guest SSH/Python access | PVE bundle for `apply` |
| `tailnet` | Debian base and Tailscale routing | Tailnet bundle |
| `apps-host` | Debian base, Docker, DNS policy, data directories, release launcher, and PVE certificate trust | None |

For routine hosted runs, select the unit in
[infra.yml](../.github/workflows/infra.yml). Manual controller commands are in
[setup](../docs/setup.md#configure-the-hosts). Application
configuration and deployment belong to the
[Compose package](../apps/compose/homelab/README.md).

## Check PVE changes

| Mode | Result |
| --- | --- |
| `plan` | Report differences and root-storage headroom |
| `audit` | Report differences and fail if managed settings have drifted |
| `apply` | Export existing LXC configuration and apply approved changes |

Plan and audit need no component bundle. Review the plan before
[applying it](../docs/setup.md#provision-the-containers).

Missing LXCs can be created and root disks can grow. Destructive changes require
`pve_lxc_reconcile_allow_destructive_vmid`; replacement requires
`pve_lxc_reconcile_allow_replacement_vmid`. Supply the exact VMID from the plan
only after arranging backups and a maintenance window.

Reconciliation protects the tailnet connection used by the controller. A change
that would restart or replace that connection must run from the
[PVE console recovery path](../docs/recovery.md#when-tailnet-is-unavailable).

Use the [recovery and storage guide](../docs/recovery.md#storage-maintenance) for live
storage changes. Declaring a smaller capacity does not shrink an existing
filesystem safely.
