#!/bin/sh
set -eu

usage() {
  echo "usage: $0 --check|--delete /var/lib/homelab/minecraft" >&2
}

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi

mode="$1"
target="$2"
expected_target="/var/lib/homelab/minecraft"
expected_parent="/var/lib/homelab"

case "${mode}" in
  --check|--delete) ;;
  *) usage; exit 2 ;;
esac

case "${target}" in
  *'
'*)
    echo "Refusing retired Minecraft data path containing a newline" >&2
    exit 1
    ;;
esac

if [ "${target}" != "${expected_target}" ]; then
  printf 'Refusing retired Minecraft data path: %s\n' "${target}" >&2
  exit 1
fi

if [ "${mode}" = "--check" ]; then
  printf '%s\n' "${target}"
  exit 0
fi

if [ ! -d "${expected_parent}" ] || [ -L "${expected_parent}" ]; then
  printf 'Refusing missing or aliased data parent: %s\n' "${expected_parent}" >&2
  exit 1
fi

resolved_parent="$(readlink -f -- "${expected_parent}")"
if [ "${resolved_parent}" != "${expected_parent}" ]; then
  printf 'Refusing noncanonical data parent: %s\n' "${resolved_parent}" >&2
  exit 1
fi

if ! mountpoint -q -- "${expected_parent}"; then
  printf 'Refusing unmounted data parent: %s\n' "${expected_parent}" >&2
  exit 1
fi

if [ -L "${target}" ]; then
  printf 'Refusing symlinked retired Minecraft data path: %s\n' "${target}" >&2
  exit 1
fi

if [ ! -e "${target}" ]; then
  echo changed=no
  exit 0
fi

if mountpoint -q -- "${target}"; then
  printf 'Refusing mounted retired Minecraft data path: %s\n' "${target}" >&2
  exit 1
fi

resolved_target="$(readlink -f -- "${target}")"
if [ "${resolved_target}" != "${expected_target}" ]; then
  printf 'Refusing noncanonical retired Minecraft data path: %s\n' \
    "${resolved_target}" >&2
  exit 1
fi

rm -rf --one-file-system -- "${target}"
if [ -e "${target}" ] || [ -L "${target}" ]; then
  printf 'Retired Minecraft data path still exists: %s\n' "${target}" >&2
  exit 1
fi

echo changed=yes
