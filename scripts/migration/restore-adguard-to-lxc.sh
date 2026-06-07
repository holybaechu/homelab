#!/bin/sh
set -eu

DNS_LXC="${DNS_LXC:-root@192.168.0.3}"
BACKUP_DIR="${BACKUP_DIR:-./migration-backups/adguard}"
CONFIG_FILE="${BACKUP_DIR}/conf/AdGuardHome.yaml"

case "${BACKUP_DIR}" in
  ""|"/"|".")
    echo "Refusing unsafe BACKUP_DIR: ${BACKUP_DIR}" >&2
    exit 1
    ;;
esac

if [ ! -d "${BACKUP_DIR}/conf" ] || [ ! -d "${BACKUP_DIR}/work" ]; then
  echo "Missing AdGuard backup directories under ${BACKUP_DIR}" >&2
  echo "Run scripts/migration/backup-adguard.sh first or set BACKUP_DIR to the extracted backup." >&2
  exit 1
fi

if [ ! -f "${CONFIG_FILE}" ]; then
  echo "Missing restored AdGuard config: ${CONFIG_FILE}" >&2
  exit 1
fi

require_config_line() {
  pattern="$1"
  description="$2"

  if ! grep -Eq "${pattern}" "${CONFIG_FILE}"; then
    echo "Refusing to restore stale AdGuard config: missing ${description}" >&2
    echo "Edit ${CONFIG_FILE} before restore and set:" >&2
    echo "  tls.certificate_path: /opt/adguardhome/tls/fullchain.pem" >&2
    echo "  tls.private_key_path: /opt/adguardhome/tls/privkey.pem" >&2
    echo "  tls.server_name: dns.hchu.me" >&2
    echo "  trusted_proxies includes: 192.168.0.4/32" >&2
    exit 1
  fi
}

require_config_line '^[[:space:]]*certificate_path:[[:space:]]*/opt/adguardhome/tls/fullchain\.pem[[:space:]]*$' "certificate_path: /opt/adguardhome/tls/fullchain.pem"
require_config_line '^[[:space:]]*private_key_path:[[:space:]]*/opt/adguardhome/tls/privkey\.pem[[:space:]]*$' "private_key_path: /opt/adguardhome/tls/privkey.pem"
require_config_line '^[[:space:]]*server_name:[[:space:]]*dns\.hchu\.me[[:space:]]*$' "server_name: dns.hchu.me"
require_config_line '^[[:space:]]*-[[:space:]]*192\.168\.0\.4/32[[:space:]]*$' "trusted proxy 192.168.0.4/32"

rsync -a "${BACKUP_DIR}/conf/" "${DNS_LXC}:/opt/adguardhome/conf/"
rsync -a "${BACKUP_DIR}/work/" "${DNS_LXC}:/opt/adguardhome/work/"
ssh "${DNS_LXC}" "chown -R homelab:homelab /opt/adguardhome/conf /opt/adguardhome/work && rc-service adguardhome restart"
