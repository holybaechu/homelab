# OpenClaw CTF sandbox boundary

OpenClaw runs one Gateway Compose service in the dedicated OpenClaw LXC. The
Gateway creates one CTF container per configured session on that LXC's local
Docker daemon. There is no second Gateway, relay, bot, or production image
build path.

## Immutable images

The complete OpenClaw descriptor records exact Gateway and CTF
`repository@sha256` identities. Two independent pinned Buildx jobs build the
images in parallel, run image-local checks, publish provenance and SBOM
attestations, and return those digests. Production receives only the runtime
package, exact private-config commit, and exact image identities; it pulls and
starts with `--no-build`.

The CTF image runs as UID/GID 1000 and includes its prevalidated analysis tool
set. It has no Docker client, host socket, release credential, or mutable image
reference. Package changes require a reviewed image build and new digest.

## Isolation

The Gateway runs as UID/GID 1000 with a read-only root filesystem and all
Linux capabilities dropped. It has the numeric host Docker group and the host
Docker socket because it launches the session sandboxes. That socket is a
powerful host boundary, which is why the Gateway remains isolated from the apps
and tailnet hosts.

Each CTF container receives only its workspace, package/browser caches, and
selected sandbox skills. The CTF bridge rejects private, loopback, link-local,
and tailnet destinations. The PVE workspace bind mount uses the declared
unprivileged UID map. The apps proxy is the only accepted Gateway ingress.

## Configuration and credentials

The checked private configuration keeps numeric Discord authorization values,
explicit agent bindings, per-thread sessions, disabled cross-context message
selection, disabled elevated tools, and the approved CTF image identity. The
runtime engine mounts the exact private configuration read-only.

The Gateway, Discord, and Exa credentials arrive as one validated
`openclaw` component bundle and become three mode-0600 files in the active
runtime slot. Secret values and their hashes are absent from the release
descriptor and state.

Skill collection and promotion run in scheduled CI in the private
`openclaw-setup` repository. The production LXC has no GitHub promotion
credential, collector account, timer, or installed sync program.

## Verification and recovery

The package smoke proves `/readyz`, requires unauthenticated and wrong-token
control requests to be rejected, and proves one bearer-authenticated request
after Compose has verified the exact running image. A deployment becomes current
only after that smoke passes. Failure and interrupted-operation recovery
recreate the previous immutable source with the current component bundle.

Run:

```sh
/usr/local/libexec/homelab-release audit --target openclaw
/usr/local/libexec/homelab-release rollback --target openclaw
```

After an intentional private-config or image promotion, additionally verify
that a configured CTF channel creates its sandbox from the descriptor's exact
CTF digest and cannot reach a denied management/private destination.
