# Talos provisioning

Use OpenTofu directly for plans and applies. `scripts/talos-preflight` only inspects disk identities; it never writes to a node. CI runs formatting, configuration and Kubernetes schema checks. The manual workflow input `talos` also renders and validates a machine configuration using temporary local credentials and state.

## Private state

Keep the existing private state directory and its existing passphrase from 1Password. The native encryption configuration retains the original `homelab` key provider name. No state migration or replacement credentials are needed.

From the repository root, with `HOMELAB_STATE_DIR` pointing outside every Git checkout:

```bash
umask 077
mkdir -p "$HOMELAB_STATE_DIR"
chmod 700 "$HOMELAB_STATE_DIR"
export TF_VAR_state_passphrase="$HOMELAB_STATE_PASSPHRASE"
export TF_DATA_DIR="$HOMELAB_STATE_DIR/providers"
export TALOSCONFIG="$HOMELAB_STATE_DIR/talosconfig"
unset TF_ENCRYPTION TF_LOG TF_LOG_PATH TF_CLI_ARGS
tofu -chdir=infra/talos init -lockfile=readonly \
  -backend-config="path=$HOMELAB_STATE_DIR/terraform.tfstate"
```

The passphrase is an ephemeral input, excluded from saved plans and state. Keep it available for each OpenTofu command. Keep `TF_LOG*` and `TF_CLI_ARGS*` unset when provisioning.

## Inspect disks and review a plan

For an installed node, run `scripts/talos-preflight`. For a new node in maintenance mode, use `scripts/talos-preflight --insecure`; this also requires the protected USB to be connected. The check requires one writable SATA disk matching the exact configured identity and serial, separate from the protected USB.

For a fresh installation, generate local credentials first so the machine configuration is visible in the subsequent plan:

```bash
tofu -chdir=infra/talos apply -target=talos_machine_secrets.cluster
```

For the existing bootstrapped cluster, preserve `bootstrap=true` in plans. For an initial installation, use `bootstrap=false` until the node is installed.

```bash
serial=$(yq -r '.data.INSTALL_DISK_SERIAL' kubernetes/clusters/homelab/settings.yaml)
tofu -chdir=infra/talos plan -var=bootstrap=true \
  -var="confirm_disk_serial=$serial" -out="$HOMELAB_STATE_DIR/node.tfplan"
tofu -chdir=infra/talos show "$HOMELAB_STATE_DIR/node.tfplan"
tofu -chdir=infra/talos show -json "$HOMELAB_STATE_DIR/node.tfplan" | \
  jq -er '.planned_values.outputs.machine_configuration.value' > "$HOMELAB_STATE_DIR/machine.yaml"
talosctl validate --config "$HOMELAB_STATE_DIR/machine.yaml" --mode metal --strict
```

After reviewing the plan and rechecking the disks, apply that exact saved plan:

```bash
scripts/talos-preflight
tofu -chdir=infra/talos apply "$HOMELAB_STATE_DIR/node.tfplan"
tofu -chdir=infra/talos output -raw talosconfig > "$HOMELAB_STATE_DIR/talosconfig"
tofu -chdir=infra/talos output -raw kubeconfig > "$HOMELAB_STATE_DIR/kubeconfig"
rm -f "$HOMELAB_STATE_DIR/node.tfplan" "$HOMELAB_STATE_DIR/machine.yaml"
```

Use `--insecure` for the final disk check before a first installation. Export `talosconfig` after installation, then create a separate reviewed plan with `bootstrap=true` to bootstrap and obtain `kubeconfig`. Preconditions reject invalid disk identities, overlapping networks, invalid node settings and unacknowledged installation serials; existing `prevent_destroy` guards remain enabled.

## Application configuration

ESO renders credentials into Secret-backed configuration files. Copyparty mounts its generated file directly. AdGuard retains its writable configuration and UI edits while the declared input is unchanged; a changed declared configuration replaces it, matching the previous behavior. Its existing `.declared.yaml` marker is preserved across this migration. qBittorrent merges defaults, saved settings and ESO-managed settings in that order, preserving UI changes except for explicitly managed authentication and listener settings.
