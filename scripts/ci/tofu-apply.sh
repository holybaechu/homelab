#!/bin/sh
set -eu

cd infra/opentofu/envs/prod
test -f prod.tfplan
tofu apply -auto-approve prod.tfplan
