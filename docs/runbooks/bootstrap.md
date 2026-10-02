# Bootstrap or rebuild the homelab

Run from the repository root on a trusted Linux/WSL controller. PVE must already
be installed and reachable. When tailnet is absent, use the LAN or an
out-of-band controller; the hosted workflow depends on tailnet for its own
connection. This repository currently provisions LXCs, not VMs.

On PVE, confirm the bridge and root datastore declared in topology exist and
that every declared `template_file_id` is already cached on its storage. The
reconciler passes that template directly to `pct create`; it does not download
images. Use PVE's template management to obtain the exact declared Debian
archive, then confirm durable storage prerequisites using
[storage guidance](homelab-storage-resize.md).

## Prepare the controller

Use Python 3.14 in a local environment and install:

```sh
python -m pip install -r requirements-deploy.txt
export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
```

The controller needs SSH access to PVE as root, a deployment key at
`~/.ssh/id_ed25519`, and PVE's independently verified host key in
`~/.ssh/known_hosts`. PVE apply installs that key's public identity into the
LXCs. Prepare mode-0600 PVE and tailnet JSON bundles outside the checkout using
[the component schemas](../../secrets/README.md). Confirm topology and storage
match the actual host, and back up existing data before an apply.

## Provision and establish access

Preview without a secret bundle:

```sh
ansible-playbook -i infra/ansible/inventory/prod/topology.json \
  infra/ansible/playbooks/reconcile.yml \
  -e homelab_unit=pve -e pve_lxc_reconcile_mode=plan
```

After reviewing the plan, apply from the same checkout:

```sh
ansible-playbook -i infra/ansible/inventory/prod/topology.json \
  infra/ansible/playbooks/reconcile.yml \
  -e homelab_unit=pve -e pve_lxc_reconcile_mode=apply \
  -e homelab_secret_bundle=/absolute/private/path/pve.json
```

Apply exports existing LXC configuration, creates missing LXCs, and bootstraps
SSH/Python access. It reads LXC host keys through trusted `pct` and updates the
controller's known-host set. Destructive or replacement changes require an
explicit exact VMID; follow [infrastructure guidance](../../infra/README.md).
Update GitHub's `DEPLOY_SSH_KNOWN_HOSTS` with the verified keys before hosted
reconciliation or runtime releases.

## Configure the hosts

Run one explicit reconciliation at a time:

```sh
ansible-playbook -i infra/ansible/inventory/prod/topology.json \
  infra/ansible/playbooks/reconcile.yml -e homelab_unit=tailnet \
  -e homelab_secret_bundle=/absolute/private/path/tailnet.json
ansible-playbook -i infra/ansible/inventory/prod/topology.json \
  infra/ansible/playbooks/reconcile.yml -e homelab_unit=apps-host
```

Tailnet reconciliation establishes management networking. Apps-host runs one
role for Docker/DNS policy, durable directories, the stable launcher and PVE
certificate trust. Both share the Debian base. Application secrets and
activation are owned by the runtime workflows. Rebuilding an existing host also requires restoring
its durable data from independent backups;
follow [recovery](recovery.md) before starting applications.

## Activate applications

Configure the GitHub production environment's component bundles and connection
credentials using [the workflow contract](github-actions.md#production-environment-contract).
Keep the apps lane held until host configuration and launcher installation
have completed. Dispatch `apps.yml` with `operation=deploy`, then run the apps
host's [release audit](compose-release.md#audit-and-rollback).

Daily application edits use those same runtime lanes. Host changes use the
single targeted Ansible entrypoint; credential-only rotations use
`sync-secrets`. [Launcher updates](compose-release.md#launcher-updates) have a
host-first order, while ordinary engine updates travel with the package.
