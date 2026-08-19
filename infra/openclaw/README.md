# OpenClaw immutable release

Production retains one dedicated `openclaw` LXC, but it no longer assembles
Node, npm packages, plugins, patches, or the CTF tool image. CI builds the two
OCI images from exact base digests and promotion supplies:

* exact Gateway and CTF `repository@sha256` references;
* a deterministic runtime tar digest;
* an exact private-config Git commit and deterministic config tar digest; and
* the exact homelab deployment commit.

`scripts/ci/openclaw_release.py` creates and verifies that canonical manifest.
`scripts/ci/deploy_openclaw_release.py` materializes it on the LXC, pulls both
digests, activates Compose, waits for readiness, performs exactly one
authenticated smoke, and then atomically replaces `release-state.json`. A failed activation
restores the previous digest/config pair before reporting failure.

The deployer `audit` command is non-mutating and does not repeat the
authenticated smoke. The `recovery` command exports a hash-complete offline
contract for the runtime/config bundles and both OCI archives; it never treats
a mutable registry tag as rebuild evidence.
