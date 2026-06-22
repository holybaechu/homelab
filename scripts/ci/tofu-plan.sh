#!/bin/sh
set -eu

cd infra/opentofu/envs/prod

: "${TOFU_STATE_BUCKET:?set TOFU_STATE_BUCKET}"
: "${TOFU_STATE_KEY:?set TOFU_STATE_KEY}"
: "${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}"
: "${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"

TOFU_STATE_REGION="${TOFU_STATE_REGION:-auto}"

if [ -n "${PROXMOX_ENDPOINT:-}${PROXMOX_API_TOKEN:-}${DEPLOY_SSH_PUBLIC_KEYS:-}" ]; then
  python3 ../../../../scripts/ci/write_tofu_vars.py ci.auto.tfvars.json
fi

set --   -backend-config="bucket=${TOFU_STATE_BUCKET}"   -backend-config="key=${TOFU_STATE_KEY}"   -backend-config="region=${TOFU_STATE_REGION}"   -backend-config="skip_credentials_validation=true"   -backend-config="skip_region_validation=true"   -backend-config="skip_metadata_api_check=true"   -backend-config="skip_s3_checksum=true"   -backend-config="use_lockfile=true"

if [ -n "${TOFU_STATE_ENDPOINT:-}" ]; then
  set -- "$@"     -backend-config="endpoint=${TOFU_STATE_ENDPOINT}"     -backend-config="use_path_style=true"
fi

tofu init -input=false "$@"
tofu fmt -recursive -check ../..
tofu validate

state_list="$(tofu state list 2>/dev/null || true)"
if [ -z "${state_list}" ] && [ "${ALLOW_EMPTY_STATE_BOOTSTRAP:-false}" != "true" ]; then
  echo "OpenTofu remote state is empty; refusing to plan production apply." >&2
  echo "Check TOFU_STATE_BUCKET/TOFU_STATE_KEY/TOFU_STATE_ENDPOINT or set ALLOW_EMPTY_STATE_BOOTSTRAP=true for first bootstrap." >&2
  exit 1
fi

tofu plan -input=false -out=prod.tfplan
tofu show -json prod.tfplan | python3 ../../../../scripts/ci/check_tofu_plan_safe.py
