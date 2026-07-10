#!/bin/sh
set -eu

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"

interval="${BACKUP_INTERVAL_SECONDS:-86400}"
keep_daily="${BACKUP_KEEP_DAILY:-7}"
keep_weekly="${BACKUP_KEEP_WEEKLY:-5}"
keep_monthly="${BACKUP_KEEP_MONTHLY:-12}"

if ! restic cat config >/dev/null 2>&1; then
  restic init
fi

while true; do
  restic unlock || true
  restic backup \
    --host homelab-docker \
    --tag homelab-compose \
    /sources/qbittorrent-config \
    /sources/downloads \
    /sources/copyparty \
    /sources/copyparty-state
  restic forget \
    --host homelab-docker \
    --tag homelab-compose \
    --keep-daily "$keep_daily" \
    --keep-weekly "$keep_weekly" \
    --keep-monthly "$keep_monthly" \
    --prune
  date -u +%FT%TZ > /state/last-success
  sleep "$interval"
done
