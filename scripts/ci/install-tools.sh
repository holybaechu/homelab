#!/bin/sh
set -eu

sudo apt-get update
sudo apt-get install -y curl unzip python3-pip python3-venv openssh-client

ANSIBLE_VENV="${HOME}/.local/ansible-venv"
python3 -m venv "${ANSIBLE_VENV}"
"${ANSIBLE_VENV}/bin/python" -m pip install --upgrade pip
"${ANSIBLE_VENV}/bin/python" -m pip install ansible
export PATH="${ANSIBLE_VENV}/bin:${PATH}"

if [ -n "${GITHUB_PATH:-}" ]; then
  printf '%s\n' "${ANSIBLE_VENV}/bin" >> "${GITHUB_PATH}"
fi

# renovate: datasource=github-releases depName=opentofu/opentofu versioning=semver extractVersion=^v(?<version>.*)$
TOFU_VERSION="${TOFU_VERSION:-1.12.3}"
curl -fsSLo /tmp/tofu.zip "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_amd64.zip"
sudo unzip -o /tmp/tofu.zip tofu -d /usr/local/bin
tofu version
ansible --version
