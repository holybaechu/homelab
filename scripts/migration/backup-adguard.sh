#!/bin/sh
set -eu

OLD_HOST="${OLD_DOCKER_HOST:?set OLD_DOCKER_HOST to the old Docker host, for example root@192.168.0.2}"
BACKUP_DIR="${BACKUP_DIR:-./migration-backups/adguard}"

case "${BACKUP_DIR}" in
  ""|"/"|".")
    echo "Refusing unsafe BACKUP_DIR: ${BACKUP_DIR}" >&2
    exit 1
    ;;
esac

mkdir -p "${BACKUP_DIR}"

rsync -a --delete "${OLD_HOST}:/home/docker/data/adguard/conf/" "${BACKUP_DIR}/conf/"
rsync -a --delete "${OLD_HOST}:/home/docker/data/adguard/work/" "${BACKUP_DIR}/work/"

tar -C "${BACKUP_DIR}/.." -czf "${BACKUP_DIR}.tar.gz" adguard
echo "AdGuard backup written to ${BACKUP_DIR}.tar.gz"
