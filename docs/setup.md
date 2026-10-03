# Setup

Run these commands from the repository root on a trusted Linux or WSL controller.
If tailnet is unavailable, connect over the LAN or through the PVE console.
Hosted workflows need tailnet to reach the hosts.

## Before you start

- Install PVE and verify root SSH access from the controller.
- Check the bridge, root datastore, and host settings in
  [topology.json](../infra/ansible/inventory/prod/topology.json).
- Cache each declared `template_file_id` in PVE. The reconciler uses that archive
  directly; it does not download templates.
- Check [shared storage](recovery.md#storage-maintenance) and back up existing data.
  A rebuild also needs the [data restoration steps](recovery.md#rebuild-a-host).

## Prepare the controller

Use Python 3.14 and a local Python environment:

```sh
python -m pip install -r requirements-deploy.txt
export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
```

The Ansible configuration selects the production inventory and role directory.

Put the deployment private key at `~/.ssh/id_ed25519` and independently verify
PVE's SSH host key before adding it to `~/.ssh/known_hosts`. PVE apply installs
the deployment key's public identity in the guests.

Prepare private, mode-`0600` `pve.json` and `tailnet.json` files using the
[component schemas](../secrets/README.md). Replace the example absolute bundle
paths below with their actual locations.

## Provision the containers

1. Preview the changes. This step needs no bundle:

   ```sh
   ansible-playbook \
     infra/ansible/playbooks/reconcile.yml \
     -e homelab_unit=pve -e pve_lxc_reconcile_mode=plan
   ```

2. Review the plan, then apply from the same checkout:

   ```sh
   ansible-playbook \
     infra/ansible/playbooks/reconcile.yml \
     -e homelab_unit=pve -e pve_lxc_reconcile_mode=apply \
     -e homelab_secret_bundle=/absolute/private/path/pve.json
   ```

   Apply exports existing LXC configuration before changing it, prepares shared
   storage, and establishes guest SSH/Python access. For destructive changes or
   replacement, follow the [PVE confirmation rules](../infra/README.md#check-pve-changes).
   Tailnet connection changes need the
   [console recovery path](recovery.md#when-tailnet-is-unavailable).

3. Update GitHub's `DEPLOY_SSH_KNOWN_HOSTS` with the verified guest keys before
   running hosted jobs. Apply reads guest keys through trusted `pct` and updates
   the controller's known-host file. A hosted apply fails its trust check until
   its supplied keys match; use the verified public lines in the job summary.

## Configure the hosts

Run each unit separately:

```sh
ansible-playbook \
  infra/ansible/playbooks/reconcile.yml -e homelab_unit=tailnet \
  -e homelab_secret_bundle=/absolute/private/path/tailnet.json
ansible-playbook \
  infra/ansible/playbooks/reconcile.yml -e homelab_unit=apps-host
```

The first run establishes management networking. The second prepares Docker,
DNS, persistent directories, the release launcher, and PVE certificate trust.
Application credentials are installed by the apps workflow.

For a rebuilt host, restore durable data before starting applications.

## Start the applications

1. Configure the [GitHub `prod` environment](operations.md#production-environment).
2. Finish host configuration before allowing the apps job to run.
3. Dispatch `apps.yml` with `operation=deploy`.
4. Run the [release audit](operations.md#audit-and-rollback) on `docker_apps`.

For later changes, use [release operations](operations.md) or reconcile the
affected infrastructure unit. Launcher changes require the
[installation order](operations.md#launcher-updates) documented there.
