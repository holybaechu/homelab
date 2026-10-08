# Homelab

Configuration and operating guides for two unprivileged Proxmox containers:
`tailnet` provides management access, and `docker_apps` runs the applications.

Host addresses, VMIDs, resources, and mounts are defined in
[topology.json](infra/ansible/inventory/prod/topology.json). Ansible provisions
and configures the hosts; Docker Compose deploys the applications.

## Getting started

Follow [setup](docs/setup.md) to set up or rebuild the homelab.
Prepare credentials using the [secrets guide](secrets/README.md).

## Common tasks

| Task | Guide |
| --- | --- |
| Change a service or its configuration | [Application package](apps/compose/homelab/README.md) |
| Change or check a host | [Infrastructure](infra/README.md) |
| Deploy, rotate credentials, audit, or roll back apps | [Operations](docs/operations.md) |
| Recover a host, restore data, or resize storage | [Recovery and storage](docs/recovery.md) |

Keep private bundles and backups outside this repository. Deployments retain
application data, but they do not replace independent backups.

## Validate locally

Run from the repository root on Linux or WSL with Bash, Python 3.14, Docker
Compose, and SSH tools. Install dependencies in a local Python environment:

```sh
python -m pip install -r requirements-dev.txt -r requirements-deploy.txt
python -m pytest -q
./scripts/ci/validate-compose.sh
export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
for unit in pve tailnet apps-host; do
  ansible-playbook \
    infra/ansible/playbooks/reconcile.yml --syntax-check \
    -e "homelab_unit=$unit" \
    -e homelab_secret_bundle=/tmp/not-read-during-syntax-check.json
done
```

Run pytest and Compose rendering for app changes; add Ansible syntax checks for
infrastructure changes. These checks do not contact production. CI runs the
retained safety tests once and skips infrastructure syntax checks for app-only PRs.
