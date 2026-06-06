#!/bin/sh
set -eu

ZONE_ID="${CLOUDFLARE_ZONE_ID}"
TOKEN="${CLOUDFLARE_DDNS_TOKEN}"
RECORDS="${DDNS_RECORD_NAMES}"

PUBLIC_IP="$(curl -fsS https://api.ipify.org)"

for RECORD in ${RECORDS}; do
  RECORD_ID="$(curl -fsS \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?type=A&name=${RECORD}" \
    | sed -n 's/.*"id":"\([^"]*\)".*/\1/p' | head -n 1)"

  if [ -z "${RECORD_ID}" ]; then
    echo "No Cloudflare A record found for ${RECORD}" >&2
    exit 1
  fi

  curl -fsS -X PATCH \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data "{\"type\":\"A\",\"name\":\"${RECORD}\",\"content\":\"${PUBLIC_IP}\",\"ttl\":120,\"proxied\":false}" \
    "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records/${RECORD_ID}" >/dev/null

  echo "Updated ${RECORD} to ${PUBLIC_IP}"
done
