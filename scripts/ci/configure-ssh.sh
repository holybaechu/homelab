#!/bin/sh
set -eu

: "${DEPLOY_SSH_PRIVATE_KEY:?set DEPLOY_SSH_PRIVATE_KEY}"
: "${PVE_TAILSCALE_IP:?set PVE_TAILSCALE_IP}"

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
printf '%s\n' "${DEPLOY_SSH_PRIVATE_KEY}" > "${HOME}/.ssh/id_ed25519"
chmod 600 "${HOME}/.ssh/id_ed25519"
touch "${HOME}/.ssh/known_hosts"
chmod 600 "${HOME}/.ssh/known_hosts"

ssh-keyscan -H -T 10 "${PVE_TAILSCALE_IP}" >> "${HOME}/.ssh/known_hosts"
ssh-keyscan -H -T 10 192.168.0.10 192.168.0.11 192.168.0.12 192.168.0.13 192.168.0.14 >> "${HOME}/.ssh/known_hosts"

eval "$(ssh-agent -s)"
ssh-add "${HOME}/.ssh/id_ed25519"

if [ -n "${GITHUB_ENV:-}" ]; then
  {
    printf 'SSH_AUTH_SOCK=%s\n' "${SSH_AUTH_SOCK}"
    printf 'SSH_AGENT_PID=%s\n' "${SSH_AGENT_PID}"
  } >> "${GITHUB_ENV}"
fi
