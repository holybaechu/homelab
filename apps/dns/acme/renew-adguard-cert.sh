#!/bin/sh
set -eu

export CF_DNS_API_TOKEN="${CLOUDFLARE_ADGUARD_ACME_TOKEN}"

DOMAIN="${ADGUARD_CERT_DOMAIN:-adguard.home.hchu.me}"
CERT_DIR="${ADGUARD_TLS_DIR:-/opt/adguardhome/tls}"
TLS_GROUP="${ADGUARD_TLS_GROUP:-homelab}"
LEGO_PATH="${LEGO_PATH:-/var/lib/lego}"
ACME_EMAIL="${ACME_EMAIL:-holybaechu@proton.me}"

mkdir -p "${CERT_DIR}" "${LEGO_PATH}"

if [ -f "${LEGO_PATH}/certificates/${DOMAIN}.crt" ]; then
  lego \
    --dns cloudflare \
    --domains "${DOMAIN}" \
    --email "${ACME_EMAIL}" \
    --path "${LEGO_PATH}" \
    renew --days 30
else
  lego \
    --accept-tos \
    --dns cloudflare \
    --domains "${DOMAIN}" \
    --email "${ACME_EMAIL}" \
    --path "${LEGO_PATH}" \
    run
fi

cp "${LEGO_PATH}/certificates/${DOMAIN}.crt" "${CERT_DIR}/fullchain.pem"
cp "${LEGO_PATH}/certificates/${DOMAIN}.key" "${CERT_DIR}/privkey.pem"
chown root:"${TLS_GROUP}" "${CERT_DIR}" "${CERT_DIR}/fullchain.pem" "${CERT_DIR}/privkey.pem"
chmod 0750 "${CERT_DIR}"
chmod 0640 "${CERT_DIR}/fullchain.pem" "${CERT_DIR}/privkey.pem"

if [ -x /etc/init.d/adguardhome ]; then
  rc-service adguardhome restart
  rc-service adguardhome status
else
  echo "AdGuard Home service is not installed yet; certificate staged"
fi
