#!/bin/sh
set -eu

sudo apt-get update
sudo apt-get install -y curl unzip python3-pip python3-venv openssh-client

ANSIBLE_VENV="${HOME}/.local/ansible-venv"
python3 -m venv "${ANSIBLE_VENV}"
"${ANSIBLE_VENV}/bin/python" -m pip install --upgrade pip
"${ANSIBLE_VENV}/bin/python" -m pip install -r requirements-deploy.txt
export PATH="${ANSIBLE_VENV}/bin:${PATH}"

if [ -n "${GITHUB_PATH:-}" ]; then
  printf '%s
' "${ANSIBLE_VENV}/bin" >> "${GITHUB_PATH}"
fi

./scripts/ci/install-opentofu.sh
# renovate: datasource=github-releases depName=opentofu/opentofu versioning=semver extractVersion=^v(?<version>.*)$
ansible --version
