#!/bin/sh
set -eu
umask 027
# Capture one projected version; keep source bytes out of argv and diagnostics.
json=$(cat "$1")
printf '%s' "$json" | jq -e 'type == "object" and (keys == ["host","password","port","protocol","username"]) and .host == "proxy.hchu.me" and .port == 443 and .protocol == "https" and (.username | type == "string" and test("\\A[A-Za-z0-9_-]{1,128}\\z")) and (.password | type == "string" and test("\\A[!-~]{20,128}\\z"))' >/dev/null 2>&1
username=$(printf '%s' "$json" | jq -r .username)
verifier=$(printf '%s' "$json" | jq -r .password | openssl passwd -6 -stdin 2>/dev/null)
case "$verifier" in '$6$'*) ;; *) exit 1 ;; esac
printf '%s:%s\n' "$username" "$verifier" > "$2"
unset json username verifier
chmod 640 "$2"
