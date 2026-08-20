#!/bin/sh
set -eu

die() {
  echo "deploy-openclaw-via-ssh: $*" >&2
  exit 2
}

valid_sha() {
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#1}" -eq 40 ]
}

valid_host() {
  case "$1" in ''|.*|*.|*..*|*[!A-Za-z0-9.-]*) return 1 ;; esac
  [ "${#1}" -le 253 ]
}

[ "$#" -eq 4 ] || die "usage: $0 SOURCE_SHA RELEASE_JSON RUNTIME_TAR CONFIG_TAR"
source_sha=$1
manifest=$2
runtime=$3
config=$4
valid_sha "$source_sha" || die "SOURCE_SHA must be exact lowercase 40-hex"
for artifact in "$manifest" "$runtime" "$config"; do
  [ -f "$artifact" ] && [ ! -L "$artifact" ] || die "release input is not a regular file: $artifact"
done

host=${OPENCLAW_HOST:-}
valid_host "$host" || die "OPENCLAW_HOST is missing or invalid"
remote="root@${host}"
python_bin=${PYTHON_BIN:-python3}
ssh_bin=${SSH_BIN:-ssh}
scp_bin=${SCP_BIN:-scp}

verified_manifest=$("$python_bin" scripts/ci/openclaw_release.py verify "$manifest") \
  || die "release manifest failed canonical validation"
manifest_source_sha=$(printf '%s' "$verified_manifest" | "$python_bin" -c \
  'import json,sys; print(json.load(sys.stdin)["deployment_source_sha"])') \
  || die "release manifest source SHA cannot be read"
[ "$manifest_source_sha" = "$source_sha" ] \
  || die "release manifest deployment_source_sha differs from SOURCE_SHA"

nonce=$("$python_bin" -c 'import secrets; print(secrets.token_hex(12))') \
  || die "cannot create unique upload id"
case "$nonce" in *[!0-9a-f]*|'') die "unique upload id is invalid" ;; esac
remote_root="/opt/openclaw/incoming/${source_sha}-${nonce}"
"$ssh_bin" "$remote" "set -eu; test \"\$(stat -c '%u:%g %a' /opt/openclaw/incoming)\" = '0:0 700'; mkdir -m 0700 -- '$remote_root'"

cleanup() {
  "$ssh_bin" "$remote" "rm -f -- '$remote_root/release.json.upload' '$remote_root/runtime.tar.upload' '$remote_root/config.tar.upload' '$remote_root/release.json' '$remote_root/runtime.tar' '$remote_root/config.tar'; rmdir -- '$remote_root'" >/dev/null 2>&1 || :
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

"$scp_bin" "$manifest" "${remote}:${remote_root}/release.json.upload"
"$scp_bin" "$runtime" "${remote}:${remote_root}/runtime.tar.upload"
"$scp_bin" "$config" "${remote}:${remote_root}/config.tar.upload"

"$ssh_bin" "$remote" sh -s -- "$source_sha" "$remote_root" <<'REMOTE_DEPLOY'
set -eu
sha=$1
root=$2
case "$root" in "/opt/openclaw/incoming/${sha}-"*) ;; *) exit 2 ;; esac
deployer=/usr/local/libexec/deploy_openclaw_release.py
contract=/usr/local/libexec/openclaw_release.py
test "$(stat -c '%u:%g %a' /opt/openclaw)" = '0:0 755'
test "$(stat -c '%u:%g %a' /opt/openclaw/state)" = '0:0 700'
test "$(stat -c '%u:%g %a' "$deployer")" = '0:0 755'
test "$(stat -c '%u:%g %a' "$contract")" = '0:0 755'
lock=/opt/openclaw/state/.upload.lock
if [ ! -e "$lock" ]; then
  install -o root -g root -m 0600 /dev/null "$lock"
fi
test -f "$lock" && test ! -L "$lock"
test "$(stat -c '%u:%g %a' "$lock")" = '0:0 600'
exec 9<>"$lock"
flock -n 9 || { echo 'another OpenClaw upload is active' >&2; exit 2; }
for name in release.json runtime.tar config.tar; do
  upload="$root/$name.upload"
  test -f "$upload" && test ! -L "$upload"
  install -o root -g root -m 0600 "$upload" "$root/$name"
done
"$contract" verify "$root/release.json" >/dev/null
manifest_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["deployment_source_sha"])' "$root/release.json")"
test "$manifest_sha" = "$sha" || { echo 'uploaded manifest source SHA mismatch' >&2; exit 2; }
set -- "$deployer" \
  --install-root /opt/openclaw \
  --secret-root /etc/openclaw/secrets \
  --readiness-url http://127.0.0.1:18789/readyz \
  --smoke-url http://127.0.0.1:18789/api/health \
  deploy \
  --manifest "$root/release.json" \
  --runtime-archive "$root/runtime.tar" \
  --config-archive "$root/config.tar"
"$@"
set -- "$deployer" \
  --install-root /opt/openclaw \
  --secret-root /etc/openclaw/secrets \
  --readiness-url http://127.0.0.1:18789/readyz \
  audit
"$@"
REMOTE_DEPLOY
