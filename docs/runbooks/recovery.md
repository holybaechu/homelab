# Control-plane disaster recovery reference

The active branch contains only the current three-host control plane. Removed
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

1. reconcile the exact `pve` unit to recreate or audit the three LXCs;
2. copy any newly reported `pct`-verified LXC public keys into
   `DEPLOY_SSH_KNOWN_HOSTS`;
3. reconcile `tailnet`, `apps-host`, and `openclaw-host` independently;
4. restore durable data and the four component secret bundles from backup;
5. run the complete apps and OpenClaw deployment lanes; and
6. run both target audits and their semantic smoke checks.

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
