#!/usr/bin/env bash
set -euo pipefail

OP_BIN="${OP_BIN:-op}"
AGENT_BROWSER_BIN="${AGENT_BROWSER_BIN:-/opt/hermes/hermes-agent/node_modules/.bin/agent-browser}"
newrrow_home_url="https://gbsm.newrrow.com/csr-platform/home"
NEWRROW_AUTH_NAME="${NEWRROW_AUTH_NAME:-newrrow-iac-1password}"
AGENT_BROWSER_SESSION_NAME="${AGENT_BROWSER_SESSION_NAME:-newrrow-points}"
export AGENT_BROWSER_SESSION_NAME

: "${NEWRROW_USERNAME_REF:?NEWRROW_USERNAME_REF must point to a 1Password username field}"
: "${NEWRROW_PASSWORD_REF:?NEWRROW_PASSWORD_REF must point to a 1Password password field}"

if [ ! -x "$AGENT_BROWSER_BIN" ]; then
  printf 'agent-browser not executable at %s\n' "$AGENT_BROWSER_BIN" >&2
  exit 1
fi

# Read credentials through op without printing secret values.
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
  --url "$newrrow_home_url" \
  --username "$username" \
  --password-stdin >/dev/null

refresh_page_state() {
  snapshot="$($AGENT_BROWSER_BIN snapshot -i 2>/dev/null || true)"
  current_url="$($AGENT_BROWSER_BIN get url 2>/dev/null || true)"
}

click_visible_login_button() {
  local login_ref=""
  refresh_page_state
  login_ref="$(printf '%s\n' "$snapshot" | sed -n 's/.*button "로그인".*\[ref=\(e[0-9][0-9]*\)\].*/@\1/p' | head -1)"
  if [ -n "$login_ref" ]; then
    "$AGENT_BROWSER_BIN" click "$login_ref" >/dev/null 2>&1
  else
    "$AGENT_BROWSER_BIN" find text "로그인" click --exact >/dev/null 2>&1
  fi
}

wait_for_newrrow_state() {
  local _
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    refresh_page_state
    if printf '%s\n%s\n' "$current_url" "$snapshot" \
      | grep -Eiq 'login|로그인|password|비밀번호|아이디|이메일|csr-platform/(home|invitation)|뉴로우 시작하기|오늘 할 일'; then
      return 0
    fi
    "$AGENT_BROWSER_BIN" wait 1000 >/dev/null || true
  done
  refresh_page_state
}

"$AGENT_BROWSER_BIN" open "$newrrow_home_url" >/dev/null
wait_for_newrrow_state

if printf '%s\n%s\n' "$current_url" "$snapshot" | grep -Eiq 'login|로그인|password|비밀번호|아이디|이메일'; then
  if ! "$AGENT_BROWSER_BIN" auth login "$NEWRROW_AUTH_NAME" >/dev/null 2>&1; then
    click_visible_login_button || true
  fi
  "$AGENT_BROWSER_BIN" wait 5000 >/dev/null || true
  "$AGENT_BROWSER_BIN" open "$newrrow_home_url" >/dev/null || true
  wait_for_newrrow_state
fi

refresh_page_state
current_url="$($AGENT_BROWSER_BIN get url 2>/dev/null || true)"
if printf '%s\n%s\n' "$current_url" "$snapshot" | grep -Eq 'csr-platform/invitation|뉴로우 시작하기'; then
  "$AGENT_BROWSER_BIN" find text "뉴로우 시작하기" click --exact >/dev/null 2>&1 || true
  "$AGENT_BROWSER_BIN" wait 5000 >/dev/null || true
  "$AGENT_BROWSER_BIN" open "$newrrow_home_url" >/dev/null || true
  wait_for_newrrow_state
fi

current_url="$($AGENT_BROWSER_BIN get url 2>/dev/null || true)"
if printf '%s\n' "$current_url" | grep -Eiq 'login|auth\.inhrplus\.com'; then
  printf 'Newrrow login did not reach an authenticated page\n' >&2
  exit 1
fi

printf 'newrrow_login_ready\n'
printf '%s\n' "$current_url"
