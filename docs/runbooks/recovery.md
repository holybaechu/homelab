# Control-plane disaster recovery reference

The active branch contains only the current two-host control plane. Removed
one-time conversion, obsolete-host cleanup, alternate release engines, and the
previous infrastructure state adapter are not executable recovery paths on
`main`.

The immutable pre-simplification reference is Git commit
`0d1f4c31b60443825167517c0dd7dc11b08cafb4`. To inspect or archive a removed
file without restoring it into the active tree:

```sh
git show 0d1f4c31b60443825167517c0dd7dc11b08cafb4:path/to/file
git archive --format=tar --output=pre-simplification-recovery.tar \
  0d1f4c31b60443825167517c0dd7dc11b08cafb4
```

Normal recovery uses the current architecture instead:

1. reconcile the exact `pve` unit to recreate or audit the two LXCs;
2. copy any newly reported `pct`-verified LXC public keys into
   `DEPLOY_SSH_KNOWN_HOSTS`;
3. reconcile `tailnet` and `apps-host` independently;
4. restore durable data and the three component secret bundles from backup;
5. run the complete apps deployment lane; and
6. run the apps audit and their semantic smoke checks.

The `pct` reconciler exports the live configuration before mutation and blocks
replacement or destructive changes unless an operator supplies the exact VMID
approval. The Compose engine keeps SHA-addressed source plus `current` and
`previous` state, and rollback always re-materializes with the current secret
bundle.

The hosted PVE job itself reaches the LAN through the tailnet LXC, so it also
protects that VMID from connectivity-affecting apply operations. If tailnet is
absent or its network/features require a restart or replacement, run the same
installed `pve-lxc-reconcile` command from the PVE console or another verified
out-of-band management path. Re-establish tailnet and update its verified SSH
host key before returning to hosted workflows.

Durable paths and named Compose volumes are never deleted by deployment or
host reconciliation. Data that is no longer referenced by a service must be
reviewed and removed manually only after an independent backup confirms that
it is not user data.

The only deliberately retained data-only paths from the removed T3 Code
service are `/srv/homelab/docker-apps/t3code/home` and
`/srv/homelab/docker-apps/t3code/workspaces`. No manifest, release, route,
secret, health check, or host role references them. Back them up and inspect
them before manual deletion; deployment never guesses whether they contain
user data.


## Retired resources

OpenClaw LXC 118 and its managed root disk were destroyed without a backup at
the owner's explicit request. The verified retirement run is
[36993496810](https://github.com/holybaechu/homelab/actions/runs/36993496810).
Current topology and reconciliation will not recreate it. Earlier source is
available at `75602fc6b5e4917cc4f93537149a169db0f5c627`; it is historical evidence,
not the current desired state. The separate workspace `/var/lib/homelab/openclaw-ctf`
was subsequently deleted without backup after explicit approval and checks that
no remaining LXC referenced it; see [36997186402](https://github.com/holybaechu/homelab/actions/runs/36997186402).
The apps/tailnet hosts and shared homelab storage were preserved.
