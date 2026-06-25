#!/usr/bin/env bash
set -euo pipefail

OP_BIN="${OP_BIN:-op}"
AGENT_BROWSER_BIN="${AGENT_BROWSER_BIN:-/opt/hermes/hermes-agent/node_modules/.bin/agent-browser}"
NEWRROW_BASE_URL="${NEWRROW_BASE_URL:-https://gbsm.newrrow.com}"
NEWRROW_HOME_URL="${NEWRROW_HOME_URL:-${NEWRROW_BASE_URL}/csr-platform/home}"
NEWRROW_LOGIN_URL="${NEWRROW_LOGIN_URL:-$NEWRROW_HOME_URL}"
NEWRROW_AUTH_NAME="${NEWRROW_AUTH_NAME:-newrrow-iac-1password}"
AGENT_BROWSER_SESSION_NAME="${AGENT_BROWSER_SESSION_NAME:-newrrow-points}"
export AGENT_BROWSER_SESSION_NAME

: "${NEWRROW_USERNAME_REF:?NEWRROW_USERNAME_REF must point to a 1Password username field}"
: "${NEWRROW_PASSWORD_REF:?NEWRROW_PASSWORD_REF must point to a 1Password password field}"

if [ ! -x "$AGENT_BROWSER_BIN" ]; then
  printf 'agent-browser not executable at %s\n' "$AGENT_BROWSER_BIN" >&2
  exit 1
fi

# Read credentials with op read through the configurable OP_BIN.
username="$($OP_BIN read "$NEWRROW_USERNAME_REF")"
newrrow_pw="$($OP_BIN read "$NEWRROW_PASSWORD_REF")"

if [ -z "$username" ] || [ -z "$newrrow_pw" ]; then
  printf 'Newrrow 1Password references returned empty username or password\n' >&2
  exit 1
fi

cleanup() {
  unset newrrow_pw username
  "$AGENT_BROWSER_BIN" auth delete "$NEWRROW_AUTH_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Store credentials only in agent-browser auth save's temporary profile; cleanup calls agent-browser auth delete.
printf '%s' "$newrrow_pw" | "$AGENT_BROWSER_BIN" auth save "$NEWRROW_AUTH_NAME" \
  --url "$NEWRROW_LOGIN_URL" \
  --username "$username" \
  --password-stdin >/dev/null

"$AGENT_BROWSER_BIN" open "$NEWRROW_HOME_URL" >/dev/null
snapshot="$($AGENT_BROWSER_BIN snapshot -i 2>/dev/null || true)"
current_url="$($AGENT_BROWSER_BIN get url 2>/dev/null || true)"

if printf '%s\n%s\n' "$current_url" "$snapshot" | grep -Eiq 'login|로그인|password|비밀번호|아이디|email'; then
  "$AGENT_BROWSER_BIN" auth login "$NEWRROW_AUTH_NAME" >/dev/null
  "$AGENT_BROWSER_BIN" wait 2000 >/dev/null || true
  "$AGENT_BROWSER_BIN" open "$NEWRROW_HOME_URL" >/dev/null || true
fi

printf 'newrrow_login_ready\n'
"$AGENT_BROWSER_BIN" get url 2>/dev/null || true
