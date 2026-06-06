#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-./migration-backups/copyparty}"
LEGACY_COPYPARTY_DIR="${LEGACY_COPYPARTY_DIR:-legacy/docker-stacks/copyparty}"

case "${BACKUP_DIR}" in
  ""|"/"|".")
    echo "Refusing unsafe BACKUP_DIR: ${BACKUP_DIR}" >&2
    exit 1
    ;;
esac

mkdir -p "${BACKUP_DIR}"

cp "${LEGACY_COPYPARTY_DIR}/copyparty.conf" "${BACKUP_DIR}/copyparty.conf"
cp "${LEGACY_COPYPARTY_DIR}/compose.yaml" "${BACKUP_DIR}/compose.yaml"

echo "Copyparty config backup written to ${BACKUP_DIR}"
echo "Verify the old Korean path names before migrating contest shares."
