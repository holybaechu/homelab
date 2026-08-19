#!/bin/sh
set -eu

die() {
  echo "refresh-lxc-ssh-trust: $*" >&2
  exit 2
}

valid_host() {
  case "$1" in ''|.*|*.|*..*|*[!A-Za-z0-9.-]*) return 1 ;; esac
  [ "${#1}" -le 253 ]
}

[ "$#" -eq 2 ] || die "usage: $0 LXC_HOST VMID"
lxc_host=$1
vmid=$2
valid_host "$lxc_host" || die "LXC_HOST is invalid"
case "$vmid" in ''|*[!0-9]*) die "VMID must be numeric" ;; esac
[ "$vmid" -ge 100 ] && [ "$vmid" -le 999999999 ] || die "VMID is outside the supported range"

pve_host=${PVE_SSH_HOST:-}
valid_host "$pve_host" || die "PVE_SSH_HOST is missing or invalid"
ssh_bin=${SSH_BIN:-ssh}
ssh_keygen_bin=${SSH_KEYGEN_BIN:-ssh-keygen}
known_hosts=${SSH_KNOWN_HOSTS_PATH:-"$HOME/.ssh/known_hosts"}
[ -f "$known_hosts" ] && [ ! -L "$known_hosts" ] || die "known_hosts is not a regular trusted file"

temporary=$(mktemp "${TMPDIR:-/tmp}/lxc-host-key.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
"$ssh_bin" "root@$pve_host" pct exec "$vmid" -- \
  cat /etc/ssh/ssh_host_ed25519_key.pub > "$temporary"

[ "$(wc -l < "$temporary" | tr -d ' ')" -eq 1 ] || die "Proxmox returned an invalid host-key record"
read -r kind key comment < "$temporary" || die "Proxmox returned no host key"
[ "$kind" = ssh-ed25519 ] && [ -n "$key" ] || die "Proxmox returned a non-ed25519 host key"
case "$key" in *[!A-Za-z0-9+/=]*) die "Proxmox returned malformed key data" ;; esac
printf '%s %s\n' "$kind" "$key" > "$temporary"
"$ssh_keygen_bin" -lf "$temporary" >/dev/null || die "Proxmox host key failed OpenSSH validation"

"$ssh_keygen_bin" -f "$known_hosts" -R "$lxc_host" >/dev/null 2>&1 || :
printf '%s %s %s\n' "$lxc_host" "$kind" "$key" >> "$known_hosts"
chmod 0600 "$known_hosts"
