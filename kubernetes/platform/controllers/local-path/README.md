# Local-path storage

Use Rancher's [official Kustomize base](https://github.com/rancher/local-path-provisioner/tree/v0.0.37/deploy)
with the homelab-only settings in `homelab.yaml`. The release ref pins the controller
and RBAC together; Renovate's Kustomize manager tracks it. Keep the helper image
version in the overlay. Rendering requires GitHub access, as the Flux bootstrap does.

When upgrading, run `python3 -m unittest discover -s tests -v` with the tools in
`mise.toml`, then render/substitute and validate the controllers layer as CI does.
Review upstream RBAC and workload changes, not just the image tag.

Both `reclaimPolicy: Retain` and the non-deleting teardown hook are intentional.
Keep `${DATA_PATH}`, existing resource names and the default StorageClass annotation.
The cluster layer owns the Namespace and its privileged Pod Security labels; the
upstream Namespace is deliberately excluded here. No PVC or PV migration is needed
for the initial switch: the complete controllers render is unchanged.

Rollback by reverting the overlay change to the previous standalone manifest.
Do not delete the StorageClass, PVCs, PVs or node directories to roll back. Verify
normal provisioning with a disposable claim only during an authorized deployment;
static validation does not exercise the provisioner against Talos.
