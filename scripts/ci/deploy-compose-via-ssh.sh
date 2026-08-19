#!/bin/sh
set -eu

usage() {
  echo "usage: $0 SHA" >&2
  echo "optional T3_SOURCE_SHA/T3_IMAGE_REF must be an exact same-SHA digest pair" >&2
}

die() {
  echo "deploy-compose-via-ssh: $*" >&2
  exit 2
}

valid_sha() {
  case "$1" in
    *[!0-9a-f]*) return 1 ;;
  esac
  [ "${#1}" -eq 40 ] || [ "${#1}" -eq 64 ]
}

valid_t3_ref() {
  value=$1
  prefix=ghcr.io/holybaechu/homelab-t3code@sha256:
  case "$value" in
    "$prefix"*) ;;
    *) return 1 ;;
  esac
  t3_ref_digest=${value#"$prefix"}
  case "$t3_ref_digest" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#t3_ref_digest}" -eq 64 ]
}

valid_host() {
  value="$1"
  case "$value" in
    ''|.*|*.|*..*|*[!A-Za-z0-9.-]*) return 1 ;;
  esac
  [ "${#value}" -le 253 ] || return 1

  old_ifs=$IFS
  IFS=.
  set -- $value
  IFS=$old_ifs
  for label do
    case "$label" in
      ''|-*|*-) return 1 ;;
    esac
    [ "${#label}" -le 63 ] || return 1
  done
}

valid_root() {
  value="$1"
  case "$value" in
    /|''|*[!A-Za-z0-9_./-]*|*//*|*/) return 1 ;;
    /*) ;;
    *) return 1 ;;
  esac
  case "/${value#/}/" in
    */../*|*/./*) return 1 ;;
  esac
}

roots_overlap() {
  [ "$1" = "$2" ] && return 0
  case "$1/" in "$2/"*) return 0 ;; esac
  case "$2/" in "$1/"*) return 0 ;; esac
  return 1
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

sha=$1
valid_sha "$sha" || die "SHA must be exactly 40 or 64 lowercase hexadecimal characters"

t3_source_sha=${T3_SOURCE_SHA:-}
t3_image_ref=${T3_IMAGE_REF:-}
if [ -n "$t3_source_sha" ] || [ -n "$t3_image_ref" ]; then
  [ -n "$t3_source_sha" ] && [ -n "$t3_image_ref" ] \
    || die "T3_SOURCE_SHA and T3_IMAGE_REF must be supplied together"
  valid_sha "$t3_source_sha" && [ "${#t3_source_sha}" -eq 40 ] \
    || die "T3_SOURCE_SHA must be an exact lowercase 40-character Git SHA"
  [ "$t3_source_sha" = "$sha" ] \
    || die "T3_SOURCE_SHA must equal the tested deployment SHA"
  valid_t3_ref "$t3_image_ref" \
    || die "T3_IMAGE_REF must be ghcr.io/holybaechu/homelab-t3code@sha256:<64 lowercase hex>"
fi

host=${DOCKER_APPS_HOST:-}
valid_host "$host" || die "DOCKER_APPS_HOST is missing or is not a strict DNS hostname/IPv4 token"
remote="root@${host}"

runtime_root=${RUNTIME_CONFIG_ROOT:-/etc/homelab/runtime}
release_root=${RELEASE_ROOT:-/opt/homelab/releases}
current_root=${CURRENT_ROOT:-/opt/homelab/current}
state_root=${STATE_ROOT:-/opt/homelab/deploy-state}
for root in "$runtime_root" "$release_root" "$current_root" "$state_root"; do
  valid_root "$root" || die "remote roots must be absolute, normalized, and shell-safe: $root"
done
roots_overlap "$runtime_root" "$release_root" && die "remote roots must not overlap"
roots_overlap "$runtime_root" "$current_root" && die "remote roots must not overlap"
roots_overlap "$runtime_root" "$state_root" && die "remote roots must not overlap"
roots_overlap "$release_root" "$current_root" && die "remote roots must not overlap"
roots_overlap "$release_root" "$state_root" && die "remote roots must not overlap"
roots_overlap "$current_root" "$state_root" && die "remote roots must not overlap"

git_bin=${GIT_BIN:-git}
sha256_bin=${SHA256_BIN:-sha256sum}
ssh_bin=${SSH_BIN:-ssh}
scp_bin=${SCP_BIN:-scp}
mktemp_bin=${MKTEMP_BIN:-mktemp}

repo_root=$("$git_bin" rev-parse --show-toplevel) || die "not inside a Git worktree"
[ -n "$repo_root" ] && [ -e "$repo_root/.git" ] || die "Git returned an invalid worktree root"
resolved=$("$git_bin" -C "$repo_root" rev-parse --verify "${sha}^{commit}") \
  || die "SHA is not an existing commit"
[ "$resolved" = "$sha" ] || die "SHA did not resolve to that exact commit object"

tmp_dir=$($mktemp_bin -d "${TMPDIR:-/tmp}/homelab-compose.XXXXXXXXXX") \
  || die "could not create a local temporary directory"
archive="$tmp_dir/release.tar"
remote_archive=

cleanup() {
  rm -rf -- "$tmp_dir"
  if [ -n "$remote_archive" ]; then
    "$ssh_bin" "$remote" "rm -f -- '$remote_archive'" >/dev/null 2>&1 || :
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

# The production artifact has one fixed manifest and no Docker build context.
set -- \
  scripts/ci/deploy_compose_release.py \
  scripts/ci/immutable_image_release.py \
  apps/compose/homelab
"$git_bin" -C "$repo_root" archive \
  --format=tar \
  --output="$archive" \
  "$sha" \
  -- \
  "$@"
[ -s "$archive" ] || die "Git produced an empty release archive"

digest_output=$("$sha256_bin" "$archive") || die "could not hash the release archive"
digest=${digest_output%%[[:space:]]*}
valid_sha "$digest" || die "SHA-256 command returned an invalid digest"
[ "${#digest}" -eq 64 ] || die "release archive digest is not SHA-256"

remote_archive=$("$ssh_bin" "$remote" \
  "umask 077; mktemp /tmp/homelab-compose-upload.XXXXXXXXXX.tar") \
  || die "could not allocate the remote incoming artifact"
case "$remote_archive" in
  /tmp/homelab-compose-upload.*.tar) ;;
  *) die "remote returned an untrusted incoming path" ;;
esac
remote_token=${remote_archive#/tmp/homelab-compose-upload.}
remote_token=${remote_token%.tar}
case "$remote_token" in
  ''|*[!A-Za-z0-9]*) die "remote incoming path is malformed" ;;
esac

"$scp_bin" "$archive" "${remote}:${remote_archive}"

if "$ssh_bin" "$remote" sh -s -- \
  "$sha" \
  "$digest" \
  "$remote_archive" \
  "$release_root" \
  "$current_root" \
  "$state_root" \
  "$runtime_root" \
  "$t3_source_sha" \
  "$t3_image_ref" <<'REMOTE_INSTALL'
set -eu

fail() {
  echo "remote Compose release install: $*" >&2
  exit 1
}

valid_sha() {
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#1}" -eq 40 ] || [ "${#1}" -eq 64 ]
}

valid_t3_ref() {
  value=$1
  prefix=ghcr.io/holybaechu/homelab-t3code@sha256:
  case "$value" in "$prefix"*) ;; *) return 1 ;; esac
  t3_ref_digest=${value#"$prefix"}
  case "$t3_ref_digest" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#t3_ref_digest}" -eq 64 ]
}

valid_root() {
  value=$1
  case "$value" in
    /|''|*[!A-Za-z0-9_./-]*|*//*|*/) return 1 ;;
    /*) ;;
    *) return 1 ;;
  esac
  case "/${value#/}/" in */../*|*/./*) return 1 ;; esac
}

roots_overlap() {
  [ "$1" = "$2" ] && return 0
  case "$1/" in "$2/"*) return 0 ;; esac
  case "$2/" in "$1/"*) return 0 ;; esac
  return 1
}

reject_symlink_components() {
  path=$1
  current=
  old_ifs=$IFS
  IFS=/
  set -- ${path#/}
  IFS=$old_ifs
  for component do
    current="${current}/$component"
    [ ! -L "$current" ] || fail "symlink path component: $current"
  done
}

assert_root_directory() {
  path=$1
  [ ! -L "$path" ] && [ -d "$path" ] || fail "not a trusted directory: $path"
  [ "$(stat -c %u "$path")" = 0 ] || fail "directory is not root-owned: $path"
  [ -z "$(find "$path" -maxdepth 0 -perm /022 -print)" ] \
    || fail "directory is group/world writable: $path"
}

assert_root_file() {
  path=$1
  [ ! -L "$path" ] && [ -f "$path" ] || fail "not a trusted regular file: $path"
  [ "$(stat -c %u "$path")" = 0 ] || fail "file is not root-owned: $path"
  [ -z "$(find "$path" -maxdepth 0 -perm /077 -print)" ] \
    || fail "incoming file is group/world accessible: $path"
}

[ "$#" -eq 9 ] || fail "invalid installer argument count"
sha=$1
digest=$2
incoming=$3
release_root=$4
current_root=$5
state_root=$6
runtime_root=$7
t3_source_sha=$8
t3_image_ref=$9

[ "$(id -u)" = 0 ] || fail "installer must run as root"
valid_sha "$sha" || fail "invalid release SHA"
valid_sha "$digest" && [ "${#digest}" -eq 64 ] || fail "invalid archive digest"
if [ -n "$t3_source_sha" ] || [ -n "$t3_image_ref" ]; then
  [ -n "$t3_source_sha" ] && [ -n "$t3_image_ref" ] \
    || fail "incomplete T3 approval"
  valid_sha "$t3_source_sha" && [ "${#t3_source_sha}" -eq 40 ] \
    || fail "invalid T3 source SHA"
  [ "$t3_source_sha" = "$sha" ] || fail "T3 source SHA differs from release SHA"
  valid_t3_ref "$t3_image_ref" || fail "invalid T3 immutable image reference"
fi
for root in "$release_root" "$current_root" "$state_root" "$runtime_root"; do
  valid_root "$root" || fail "invalid remote root: $root"
  reject_symlink_components "$root"
done
roots_overlap "$runtime_root" "$release_root" && fail "remote roots overlap"
roots_overlap "$runtime_root" "$current_root" && fail "remote roots overlap"
roots_overlap "$runtime_root" "$state_root" && fail "remote roots overlap"
roots_overlap "$release_root" "$current_root" && fail "remote roots overlap"
roots_overlap "$release_root" "$state_root" && fail "remote roots overlap"
roots_overlap "$current_root" "$state_root" && fail "remote roots overlap"
case "$incoming" in /tmp/homelab-compose-upload.*.tar) ;; *) fail "invalid incoming path" ;; esac
incoming_token=${incoming#/tmp/homelab-compose-upload.}
incoming_token=${incoming_token%.tar}
case "$incoming_token" in ''|*[!A-Za-z0-9]*) fail "malformed incoming path" ;; esac
extract=
cleanup_remote() {
  [ -z "$extract" ] || rm -rf -- "$extract"
  rm -f -- "$incoming"
}
trap cleanup_remote EXIT
trap 'exit 130' HUP INT TERM
reject_symlink_components "$incoming"
assert_root_file "$incoming"

if [ ! -e "$release_root" ]; then
  install -d -o root -g root -m 0755 "$release_root"
fi
assert_root_directory "$release_root"
incoming_root="$release_root/.incoming"
if [ ! -e "$incoming_root" ]; then
  install -d -o root -g root -m 0700 "$incoming_root"
fi
assert_root_directory "$incoming_root"

lock="$release_root/.upload.lock"
[ ! -L "$lock" ] || fail "upload lock is a symlink"
(umask 077; : >>"$lock")
chown root:root "$lock"
chmod 0600 "$lock"
exec 9>"$lock"
flock -x 9

remote_digest=$(sha256sum "$incoming") || fail "could not hash incoming archive"
remote_digest=${remote_digest%%[[:space:]]*}
[ "$remote_digest" = "$digest" ] || fail "incoming archive digest mismatch"

final="$release_root/$sha"
marker="$final/.archive.sha256"
if [ -e "$final" ] || [ -L "$final" ]; then
  assert_root_directory "$final"
  [ -z "$(find "$final" -xdev \( -type l -o ! -user root -o -perm /022 \) -print -quit)" ] \
    || fail "existing release tree is untrusted"
  [ ! -L "$marker" ] && [ -f "$marker" ] \
    || fail "existing release has no archive digest"
  [ "$(stat -c %u "$marker")" = 0 ] || fail "archive digest is not root-owned"
  [ "$(cat "$marker")" = "$digest" ] \
    || fail "immutable release already exists with a different archive digest"
else
  extract=$(mktemp -d "$incoming_root/extract.XXXXXXXXXX") \
    || fail "could not create extraction directory"
  python3 - "$incoming" "$extract" <<'PY_EXTRACT'
import os
from pathlib import Path
import shutil
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
seen: set[str] = set()
compose_manifests: set[str] = set()
deployer_seen = False
immutable_helper_seen = False
member_count = 0
total_size = 0


def allowed(parts: tuple[str, ...]) -> bool:
    if parts in {
        ("apps",),
        ("apps", "compose"),
        ("scripts",),
        ("scripts", "ci"),
    }:
        return True
    if parts in {
        ("scripts", "ci", "deploy_compose_release.py"),
        ("scripts", "ci", "immutable_image_release.py"),
    }:
        return True
    return (
        len(parts) >= 3
        and parts[:3] == ("apps", "compose", "homelab")
    )


with tarfile.open(archive, mode="r:") as bundle:
    members = bundle.getmembers()
    for member in members:
        member_count += 1
        if member_count > 10000:
            raise SystemExit("archive has too many entries")
        name = member.name.rstrip("/")
        raw_parts = name.split("/")
        if (
            not name
            or member.name.startswith("/")
            or "\\" in member.name
            or any(part in {"", ".", ".."} for part in raw_parts)
            or not allowed(tuple(raw_parts))
            or name in seen
            or not (member.isdir() or member.isfile())
            or member.mode & 0o7000
        ):
            raise SystemExit(f"unsafe or unexpected archive member: {member.name!r}")
        seen.add(name)
        if member.isfile():
            total_size += member.size
            if member.size < 0 or total_size > 512 * 1024 * 1024:
                raise SystemExit("archive file content exceeds the release limit")
        if name == "scripts/ci/deploy_compose_release.py" and member.isfile():
            deployer_seen = True
        if name == "scripts/ci/immutable_image_release.py" and member.isfile():
            immutable_helper_seen = True
        if len(raw_parts) == 4 and raw_parts[:2] == ["apps", "compose"] and raw_parts[3] == "compose.yml" and member.isfile():
            compose_manifests.add(raw_parts[2])

    if (
        not deployer_seen
        or not immutable_helper_seen
        or compose_manifests != {"homelab"}
    ):
        raise SystemExit(
            "archive is missing a deployment helper or the homelab Compose manifest"
        )

    for member in members:
        target = destination.joinpath(*member.name.rstrip("/").split("/"))
        if member.isdir():
            target.mkdir(mode=member.mode & 0o777, parents=True, exist_ok=True)
            os.chmod(target, member.mode & 0o777)
            continue
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit(f"cannot read archive member: {member.name!r}")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        os.chmod(target, member.mode & 0o777)
PY_EXTRACT

  printf '%s\n' "$digest" >"$extract/.archive.sha256"
  chown -R root:root "$extract"
  chmod -R go-w "$extract"
  chmod 0444 "$extract/.archive.sha256"
  mv "$extract" "$final"
  extract=
fi

# The upload lock protects only digest verification and immutable installation.
# Activation has its own per-project lock in deploy_compose_release.py.
flock -u 9
exec 9>&-

deployer="$final/scripts/ci/deploy_compose_release.py"
[ ! -L "$deployer" ] && [ -f "$deployer" ] || fail "installed deployer is unavailable"
set -- python3 "$deployer" "$sha"
set -- "$@" \
  --release-root "$release_root" \
  --current-root "$current_root" \
  --state-root "$state_root" \
  --runtime-config-root "$runtime_root"
if [ -n "$t3_image_ref" ]; then
  set -- "$@" \
    --t3-source-sha "$t3_source_sha" \
    --t3-image-ref "$t3_image_ref"
fi
"$@"
REMOTE_INSTALL
then
  remote_archive=
  printf 'Deployed homelab Compose release %s (%s) to %s\n' \
    "$sha" "$digest" "$remote"
else
  status=$?
  exit "$status"
fi
