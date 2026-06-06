#!/bin/sh
set -eu

cd infra/opentofu/envs/prod

if [ ! -f terraform.tfvars ] && [ -f terraform.tfvars.example ]; then
  cp terraform.tfvars.example terraform.tfvars
fi

tofu init
tofu fmt -recursive -check ../..
tofu validate
tofu plan -out=prod.tfplan
