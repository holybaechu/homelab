#!/bin/sh
set -eu

cd infra/opentofu/envs/prod

: "${TOFU_STATE_BUCKET:?set TOFU_STATE_BUCKET}"
: "${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}"
: "${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"

TOFU_STATE_KEY="${TOFU_STATE_KEY:-prod/opentofu.tfstate}"
TOFU_STATE_REGION="${TOFU_STATE_REGION:-auto}"

if [ ! -f terraform.tfvars ] && [ -f terraform.tfvars.example ]; then
  cp terraform.tfvars.example terraform.tfvars
fi

set -- \
  -backend-config="bucket=${TOFU_STATE_BUCKET}" \
  -backend-config="key=${TOFU_STATE_KEY}" \
  -backend-config="region=${TOFU_STATE_REGION}" \
  -backend-config="skip_credentials_validation=true" \
  -backend-config="skip_region_validation=true" \
  -backend-config="skip_metadata_api_check=true" \
  -backend-config="skip_s3_checksum=true" \
  -backend-config="use_lockfile=true"

if [ -n "${TOFU_STATE_ENDPOINT:-}" ]; then
  set -- "$@" \
    -backend-config="endpoint=${TOFU_STATE_ENDPOINT}" \
    -backend-config="use_path_style=true"
fi

tofu init "$@"
tofu fmt -recursive -check ../..
tofu validate

set -- -out=prod.tfplan

if [ -n "${TF_VAR_proxmox_endpoint:-}" ]; then
  set -- "$@" "-var=proxmox_endpoint=${TF_VAR_proxmox_endpoint}"
fi

if [ -n "${TF_VAR_proxmox_api_token:-}" ]; then
  set -- "$@" "-var=proxmox_api_token=${TF_VAR_proxmox_api_token}"
fi

if [ -n "${TF_VAR_proxmox_insecure_tls:-}" ]; then
  set -- "$@" "-var=proxmox_insecure_tls=${TF_VAR_proxmox_insecure_tls}"
fi

if [ -n "${TF_VAR_node_name:-}" ]; then
  set -- "$@" "-var=node_name=${TF_VAR_node_name}"
fi

if [ -n "${TF_VAR_bridge:-}" ]; then
  set -- "$@" "-var=bridge=${TF_VAR_bridge}"
fi

if [ -n "${TF_VAR_root_datastore_id:-}" ]; then
  set -- "$@" "-var=root_datastore_id=${TF_VAR_root_datastore_id}"
fi

if [ -n "${TF_VAR_ssh_public_keys:-}" ]; then
  set -- "$@" "-var=ssh_public_keys=${TF_VAR_ssh_public_keys}"
fi

tofu plan "$@"
