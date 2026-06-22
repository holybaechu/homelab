#!/bin/sh
set -eu

: "${CLOUDFLARE_ZONE_ID:?set CLOUDFLARE_ZONE_ID}"
: "${CLOUDFLARE_DDNS_TOKEN:?set CLOUDFLARE_DDNS_TOKEN}"
: "${DDNS_RECORD_NAMES:?set DDNS_RECORD_NAMES}"

api="https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records"
PUBLIC_IP="$(curl -fsS https://api.ipify.org)"

cf() {
	curl -fsS \
		-H "Authorization: Bearer ${CLOUDFLARE_DDNS_TOKEN}" \
		-H "Content-Type: application/json" \
		"$@"
}

for RECORD in ${DDNS_RECORD_NAMES}; do
	RECORD_ID="$(
		cf "${api}?type=A&name=${RECORD}" \
			| jq -er '.result[0].id // empty'
	)"

	if [ -z "${RECORD_ID}" ]; then
		echo "No Cloudflare A record found for ${RECORD}" >&2
		exit 1
	fi

	payload="$(
		jq -n \
			--arg name "${RECORD}" \
			--arg content "${PUBLIC_IP}" \
			'{type:"A", name:$name, content:$content, ttl:120, proxied:false}'
	)"

	cf -X PATCH --data "${payload}" "${api}/${RECORD_ID}" \
		| jq -e '.success == true' >/dev/null

	echo "Updated ${RECORD} to ${PUBLIC_IP}"
done
