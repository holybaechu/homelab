#!/bin/sh
set -eu

OLD_HOST="${OLD_DOCKER_HOST:?set OLD_DOCKER_HOST to the old Docker host, for example root@192.168.0.2}"
BACKUP_DIR="${BACKUP_DIR:-./migration-backups/qbittorrent}"

case "${BACKUP_DIR}" in
  ""|"/"|".")
    echo "Refusing unsafe BACKUP_DIR: ${BACKUP_DIR}" >&2
    exit 1
    ;;
esac

mkdir -p "${BACKUP_DIR}"

ssh "${OLD_HOST}" "docker cp qbittorrent:/config - | gzip -c" > "${BACKUP_DIR}/container-config.tar.gz"
ssh "${OLD_HOST}" "docker inspect qbittorrent" > "${BACKUP_DIR}/docker-inspect.json"

echo "qBittorrent backup written to ${BACKUP_DIR}"
