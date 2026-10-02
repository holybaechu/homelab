# Historical control-plane cutover

Use this procedure only for a host still running the pre-simplification control
plane from commit `0d1f4c31b60443825167517c0dd7dc11b08cafb4`. It documents the
one-time conversion; it is not a prerequisite for an ordinary release or a
new host. Current setup is in [bootstrap.md](bootstrap.md), and current
launcher changes are in [compose-release.md](compose-release.md).

## Original host-first activation sequence

The stable launcher is infrastructure-owned and is never uploaded by an
application workflow. A merge to `main` can automatically start a runtime
workflow, so installing the launcher after merge is too late. For the first
activation and every future `release_launcher.py` change, use this order:

1. Keep the change unmerged and check out its exact candidate commit on a
   trusted, tailnet-connected Ansible controller with pinned SSH trust.
2. From that candidate checkout, reconcile `apps-host` and `openclaw-host`
   explicitly. These out-of-band runs install the candidate launcher before any
   automatic runtime trigger:

   ```sh
   export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
   ansible-playbook -i infra/ansible/inventory/prod/topology.json \
     infra/ansible/playbooks/reconcile.yml -e homelab_unit=apps-host
   ansible-playbook -i infra/ansible/inventory/prod/topology.json \
     infra/ansible/playbooks/reconcile.yml -e homelab_unit=openclaw-host
   ```

3. Compare each host's `/usr/local/libexec/homelab-release` SHA-256 with the
   candidate `scripts/ci/release_launcher.py`; do not merge if either differs.
4. Take the documented PVE snapshot and data backup, then merge while holding
   the apps production-environment approval. The OpenClaw workflow may remain
   queued behind the shared control-plane lock.
5. The previous apps host has an externally created `homelab_proxy` network
   without Compose ownership labels. Run this bounded transition on the apps
   host in the maintenance window:

   ```sh
   previous_stack="$(readlink -e /opt/homelab/current/homelab)"
   test -d "$previous_stack"
   test -f "$previous_stack/.env"
   test -f "$previous_stack/.homelab/artifacts.env"
   docker compose --project-name homelab --project-directory "$previous_stack" \
     --env-file "$previous_stack/.env" \
     --env-file "$previous_stack/.homelab/artifacts.env" \
     -f "$previous_stack/compose.yml" down --remove-orphans
   test "$(docker network inspect homelab_proxy --format '{{len .Containers}}')" = 0
   docker network rm homelab_proxy
   ```

   Skip these commands when inspection already shows both
   `com.docker.compose.project=homelab` and
   `com.docker.compose.network=proxy`.
6. Approve the apps job immediately afterward. The new release creates the
   same named network with Compose labels. Until the transition is complete,
   the new engine detects the unowned network before image pull, `up`, or
   `down`, restores its empty pending state, and leaves the previous project
   running. The normal apps and OpenClaw workflows may then activate the new
   engine. OpenClaw keeps the
   whole workflow in the non-cancelling `prod-control-plane` queue, so its two
   image builds run in parallel within one admitted workflow. GitHub does not
   guarantee dispatch-order admission to a concurrency group, so each automatic
   runtime job compares its exact lane input paths with current homelab `main`
   immediately before mutation. Unrelated newer documentation or test commits
   do not suppress a deployment, while a newer package, topology, transport, or
   workflow change makes the older run fail without touching the host. An
   automatic OpenClaw run also checks out private-config `main` again directly
   before the deploy command and rejects a promotion whose bound commit no
   longer matches that tip. Manual dispatch remains the explicit rollback path.
7. After both first deployments and launcher audits succeed, archive the old
   control directories and retire old unit/account/executable/individual-secret
   artifacts using the immutable pre-simplification reference in
   `docs/runbooks/recovery.md`. Record that one-time host operation separately;
   current reconciliation owns only host primitives and carries no recurring
   conversion or obsolete-host cleanup branch.

On a new/rebuilt host, complete the same out-of-band host reconcile before
allowing its first runtime job. Prepare all component documents before the
merge; the host-primitives run does not install application secrets.

The control plane uses `compose-releases`, `compose-runtime`, and
`compose-control` below each target install root, so it never interprets an
unrelated state schema. Its first activation has no recorded previous release.
Take a PVE snapshot and a separate durable-data/secret backup, use a maintenance
window, then run one complete target deployment. After its audit passes,
archive or remove unreferenced older control directories; do not delete
`/srv/homelab`, `/var/lib/openclaw`, or named Compose volumes.
