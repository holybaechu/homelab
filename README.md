# Homelab

Public desired state for three unprivileged Proxmox LXCs: management networking,
the shared application host, and an isolated OpenClaw host.

## Responsibilities

| Owner | Responsibility | Starting point |
| --- | --- | --- |
| Topology | Host identities, addresses, VMIDs, resources and mounts | [topology.json](infra/ansible/inventory/prod/topology.json) |
| Ansible | Provision LXCs; configure storage, Debian, access, Docker and isolation | [Infrastructure](infra/README.md) |
| Docker Compose | Run the complete application packages | [Apps](apps/compose/homelab/README.md), [OpenClaw](infra/openclaw/README.md) |
| GitHub Actions | Build exact releases and call the shared release engine | [Deployment lanes](docs/runbooks/github-actions.md) |

Ansible owns both provisioning and host configuration. For this three-LXC
homelab, adding Terraform would add state and another ownership handoff without
replacing an existing tool. Topology remains the single host declaration;
host-specific Docker policy lives in the matching inventory `group_vars`.

The shared release engine retains exact source and image identities, semantic
smoke, automatic rollback and interrupted-operation recovery. Durable data and
current private credentials stay outside immutable releases.

## Operate

Start a new or rebuilt installation with [bootstrap](docs/runbooks/bootstrap.md).

| Task | Entry point |
| --- | --- |
| Change applications | Edit their package; the owning runtime workflow deploys it |
| Change host configuration | Edit inventory/roles; dispatch `infra.yml` for exactly one unit |
| Inspect PVE drift | Dispatch `infra.yml` with `unit=pve`, `pve_mode=plan` or `audit` |
| Rotate runtime credentials | Dispatch `apps.yml` or `openclaw.yml` with `operation=sync-secrets` |
| Audit or roll back | Use the [host release commands](docs/runbooks/compose-release.md) |
| Recover infrastructure or data | Follow [recovery](docs/runbooks/recovery.md) or [storage resize](docs/runbooks/homelab-storage-resize.md) |

Private Gateway configuration belongs in the sibling `openclaw-setup`
repository. Secret schemas and preparation are in [secrets](secrets/README.md).
Historical hosts use the separate [legacy cutover](docs/runbooks/legacy-cutover.md).

## Validate locally

Use a Linux controller or WSL with Bash, Python 3.14, Docker Compose and SSH tools.
In a local Python environment, install test dependencies from
`requirements-dev.txt` and Ansible from `requirements-deploy.txt`:

```sh
python -m pip install -r requirements-dev.txt -r requirements-deploy.txt
python -m pytest -q
./scripts/ci/validate-compose.sh
export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
for unit in pve tailnet apps-host openclaw-host; do
  ansible-playbook -i infra/ansible/inventory/prod/topology.json \
    infra/ansible/playbooks/reconcile.yml --syntax-check \
    -e "homelab_unit=$unit" \
    -e homelab_secret_bundle=/tmp/not-read-during-syntax-check.json
done
```

Compose rendering and Ansible syntax checks do not contact production. Tests
protect release recovery, destructive PVE changes, credential handling and
isolation; capacity changes are declared once in topology rather than repeated
as test snapshots.
