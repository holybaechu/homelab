#!/bin/sh
set -eu

die() {
  printf 'deploy-release-via-ssh: %s\n' "$*" >&2
  exit 2
}

valid_host() {
  value=$1
  case "$value" in
    ''|.*|*.|*..*|*[!A-Za-z0-9.-]*) return 1 ;;
  esac
  [ "${#value}" -le 253 ] || return 1
  old_ifs=$IFS
  IFS=.
  set -- $value
  IFS=$old_ifs
  for label do
    case "$label" in ''|-*|*-) return 1 ;; esac
    [ "${#label}" -le 63 ] || return 1
  done
}

valid_sha256() {
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#1}" -eq 64 ]
}

case "${1:-}" in
  deploy)
    [ "$#" -eq 4 ] \
      || die "usage: $0 deploy apps|openclaw BUNDLE.tar SECRET_BUNDLE.json"
    operation=deploy
    target=$2
    bundle=$3
    secret_bundle=$4
    ;;
  sync-secrets)
    [ "$#" -eq 3 ] \
      || die "usage: $0 sync-secrets apps|openclaw SECRET_BUNDLE.json"
    operation=sync-secrets
    target=$2
    bundle=
    secret_bundle=$3
    ;;
  *) die "first argument must be deploy or sync-secrets" ;;
esac

case "$target" in
  apps) host=${DOCKER_APPS_HOST:-} ;;
  openclaw) host=${OPENCLAW_HOST:-} ;;
  *) die "target must be apps or openclaw" ;;
esac
valid_host "$host" || die "target host is missing or invalid"
if [ "$operation" = deploy ]; then
  [ -f "$bundle" ] && [ ! -L "$bundle" ] \
    || die "bundle must be a regular non-symlink file"
fi
[ -f "$secret_bundle" ] && [ ! -L "$secret_bundle" ] \
  || die "component secret bundle must be a regular non-symlink file"

ssh_bin=${SSH_BIN:-ssh}
scp_bin=${SCP_BIN:-scp}
sha256_bin=${SHA256_BIN:-sha256sum}
mktemp_bin=${MKTEMP_BIN:-mktemp}
remote="root@${host}"

digest=
if [ "$operation" = deploy ]; then
  digest_output=$($sha256_bin "$bundle") || die "cannot hash bundle"
  digest=${digest_output%%[[:space:]]*}
  valid_sha256 "$digest" || die "SHA-256 command returned an invalid digest"
fi

local_stage=$($mktemp_bin -d "${TMPDIR:-/tmp}/homelab-release.XXXXXXXXXX") \
  || die "cannot allocate local upload staging"
cp -- "$secret_bundle" "$local_stage/secrets.json"
if [ "$operation" = deploy ]; then
  cp -- "$bundle" "$local_stage/release.tar"
fi

remote_root=
cleanup() {
  rm -rf -- "$local_stage"
  if [ -n "$remote_root" ]; then
    "$ssh_bin" "$remote" "rm -rf -- '$remote_root'" >/dev/null 2>&1 || :
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

remote_root=$(
  "$ssh_bin" "$remote" "umask 077; mktemp -d '/tmp/homelab-${target}-${operation}.XXXXXXXXXX'"
) || die "cannot allocate remote upload"
case "$remote_root" in
  "/tmp/homelab-${target}-${operation}."*) ;;
  *) die "remote returned an untrusted upload path" ;;
esac
token=${remote_root#"/tmp/homelab-${target}-${operation}."}
case "$token" in ''|*[!A-Za-z0-9]*) die "remote upload path is malformed" ;; esac

set -- "$local_stage/secrets.json"
if [ "$operation" = deploy ]; then
  set -- "$local_stage/release.tar" "$@"
fi
"$scp_bin" "$@" "${remote}:${remote_root}/" || die "release upload failed"

if [ "$operation" = deploy ]; then
  "$ssh_bin" "$remote" \
    "/usr/local/libexec/homelab-release deploy --target '$target' --archive '$remote_root/release.tar' --sha256 '$digest' --secret-bundle '$remote_root/secrets.json'" \
    || die "remote release activation failed"
  printf 'Deployed %s bundle %s to %s\n' "$target" "$digest" "$remote"
else
  "$ssh_bin" "$remote" \
    "/usr/local/libexec/homelab-release sync-secrets --target '$target' --secret-bundle '$remote_root/secrets.json'" \
    || die "remote secret sync failed"
  printf 'Synchronized %s component secrets on %s\n' "$target" "$remote"
fi
