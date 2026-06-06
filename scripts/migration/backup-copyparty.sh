#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-./migration-backups/copyparty}"

case "${BACKUP_DIR}" in
  ""|"/"|".")
    echo "Refusing unsafe BACKUP_DIR: ${BACKUP_DIR}" >&2
    exit 1
    ;;
esac

mkdir -p "${BACKUP_DIR}"

cp stacks/copyparty/copyparty.conf "${BACKUP_DIR}/copyparty.conf"
cp stacks/copyparty/compose.yaml "${BACKUP_DIR}/compose.yaml"

echo "Copyparty config backup written to ${BACKUP_DIR}"
echo "Verify the old Korean path names before migrating contest shares."
