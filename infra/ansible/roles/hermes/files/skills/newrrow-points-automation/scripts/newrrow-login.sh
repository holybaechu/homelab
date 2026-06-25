#!/usr/bin/env bash
set -euo pipefail

OP_BIN="${OP_BIN:-op}"

: "${NEWRROW_USERNAME_REF:?NEWRROW_USERNAME_REF must point to a 1Password username field}"
: "${NEWRROW_PASSWORD_REF:?NEWRROW_PASSWORD_REF must point to a 1Password password field}"

# Credential preflight only. Do not drive agent-browser from this helper:
# Newrrow is a public URL, so runtime navigation/login must stay on the Hermes
# browser tool route (Browserbase in homelab) via the newrrow_browser_login tool.
username="$($OP_BIN read "$NEWRROW_USERNAME_REF")"
newrrow_pw="$($OP_BIN read "$NEWRROW_PASSWORD_REF")"

if [ -z "$username" ] || [ -z "$newrrow_pw" ]; then
  printf 'Newrrow 1Password references returned empty username or password\n' >&2
  exit 1
fi

unset username newrrow_pw
printf 'newrrow_1password_refs_ready\n'
