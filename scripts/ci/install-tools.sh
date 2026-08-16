#!/bin/sh
set -eu

missing=""
for command in curl unzip python3 ssh; do
  command -v "${command}" >/dev/null 2>&1 || missing="${missing} ${command}"
done
if [ -n "${missing}" ]; then
  sudo apt-get update
  sudo apt-get install -y curl unzip python3-pip python3-venv openssh-client
fi

ANSIBLE_VENV="${HOME}/.local/ansible-venv"
python3 -m venv "${ANSIBLE_VENV}"
"${ANSIBLE_VENV}/bin/python" -m pip install --disable-pip-version-check -r requirements-deploy.txt
export PATH="${ANSIBLE_VENV}/bin:${PATH}"

if [ -n "${GITHUB_PATH:-}" ]; then
  printf '%s
' "${ANSIBLE_VENV}/bin" >> "${GITHUB_PATH}"
fi

if [ "${INSTALL_OPENTOFU:-true}" = "true" ]; then
  ./scripts/ci/install-opentofu.sh
fi
ansible --version
