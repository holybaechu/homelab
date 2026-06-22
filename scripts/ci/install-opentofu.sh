#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
# renovate: datasource=github-releases depName=opentofu/opentofu versioning=semver extractVersion=^v(?<version>.*)$
TOFU_VERSION="${TOFU_VERSION:-$(cat "${repo_root}/.opentofu-version")}" 
TOFU_OS="${TOFU_OS:-linux}"
TOFU_ARCH="${TOFU_ARCH:-amd64}"
TOFU_DEST_DIR="${TOFU_DEST_DIR:-/usr/local/bin}"
archive="tofu_${TOFU_VERSION}_${TOFU_OS}_${TOFU_ARCH}.zip"
base_url="https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}"
workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT HUP INT TERM

curl -fsSLo "${workdir}/${archive}" "${base_url}/${archive}"
curl -fsSLo "${workdir}/SHA256SUMS" "${base_url}/tofu_${TOFU_VERSION}_SHA256SUMS"
(
  cd "${workdir}"
  grep " ${archive}$" SHA256SUMS | sha256sum -c -
  if command -v unzip >/dev/null 2>&1; then
    unzip -q "${archive}" tofu
  else
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract(sys.argv[2])' "${archive}" tofu
  fi
)

if [ -w "${TOFU_DEST_DIR}" ]; then
  install -m 0755 "${workdir}/tofu" "${TOFU_DEST_DIR}/tofu"
else
  sudo install -m 0755 "${workdir}/tofu" "${TOFU_DEST_DIR}/tofu"
fi

"${TOFU_DEST_DIR}/tofu" version
