#!/bin/sh
set -eu

cd infra/opentofu/envs/prod
test -f prod.tfplan
test -f ci.auto.tfvars.json
tofu apply -input=false -auto-approve prod.tfplan
