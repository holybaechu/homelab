#!/bin/sh
set -eu

: "${DEPLOY_SSH_PRIVATE_KEY:?set DEPLOY_SSH_PRIVATE_KEY}"
: "${DEPLOY_SSH_KNOWN_HOSTS:?set DEPLOY_SSH_KNOWN_HOSTS}"

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
printf '%s\n' "${DEPLOY_SSH_PRIVATE_KEY}" > "${HOME}/.ssh/id_ed25519"
chmod 600 "${HOME}/.ssh/id_ed25519"
printf '%s\n' "${DEPLOY_SSH_KNOWN_HOSTS}" > "${HOME}/.ssh/known_hosts"
chmod 600 "${HOME}/.ssh/known_hosts"
ssh-keygen -l -f "${HOME}/.ssh/known_hosts" >/dev/null

eval "$(ssh-agent -s)"
ssh-add "${HOME}/.ssh/id_ed25519"

if [ -n "${GITHUB_ENV:-}" ]; then
  {
    printf 'SSH_AUTH_SOCK=%s\n' "${SSH_AUTH_SOCK}"
    printf 'SSH_AGENT_PID=%s\n' "${SSH_AGENT_PID}"
  } >> "${GITHUB_ENV}"
fi
