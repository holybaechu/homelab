#!/bin/sh
set -eu

fail() {
  printf 'homelab smoke failed: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "root execution is required"

compose() {
  docker compose --project-name homelab -f compose.yml "$@"
}

expected_app_ip="$(python3 -c '
import ipaddress, json
with open("topology.json", encoding="utf-8") as source:
    value = json.load(source)["all"]["children"]["debian"]["hosts"]["docker_apps"]["ansible_host"]
print(ipaddress.ip_address(value))
')"
dns_answers="$(dig +short +time=3 +tries=1 @127.0.0.1 qbt.home.hchu.me A)"
printf '%s\n' "${dns_answers}" | grep -Fqx "${expected_app_ip}" \
  || fail "AdGuard did not return the application host for qbt.home.hchu.me"
dig +short +time=3 +tries=1 @127.0.0.1 example.com A | grep -Eq '^[0-9]+(\.[0-9]+){3}$' \
  || fail "AdGuard did not resolve a public A record"

probe_ingress() {
  url=$1
  hostname="${url#*://}"
  hostname="${hostname%%/*}"
  attempt=1
  while [ "${attempt}" -le 10 ]; do
    if curl --fail --silent --show-error --max-time 8 --output /dev/null \
      --resolve "${hostname}:443:127.0.0.1" "${url}"
    then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  fail "shared ingress route failed for ${hostname}"
}

smoke_urls="$(
  compose config --format json | python3 -c '
import json, sys
model = json.load(sys.stdin)
for service in model.get("services", {}).values():
    labels = service.get("labels", {})
    if isinstance(labels, list):
        labels = dict(item.split("=", 1) for item in labels if "=" in item)
    url = labels.get("homelab.smoke.url")
    if url:
        print(url)
'
)"
[ -n "${smoke_urls}" ] || fail "Compose model declares no ingress smoke URLs"
printf '%s\n' "${smoke_urls}" | while IFS= read -r url; do
  probe_ingress "${url}"
done

for app_host in metube.home.hchu.me copyparty.hchu.me; do
app_auth="$(curl --silent --show-error --max-time 8 --output /dev/null \
  --write-out '%{http_code} %{redirect_url}' \
  --resolve "${app_host}:443:127.0.0.1" \
  "https://${app_host}/")" || fail "${app_host} authentication probe failed"
case "${app_auth}" in
  '302 https://auth.home.hchu.me/'*|'303 https://auth.home.hchu.me/'* \
  |"302 https://${app_host}/outpost.goauthentik.io/"* \
  |"303 https://${app_host}/outpost.goauthentik.io/"*) ;;
  *) fail "${app_host} did not require an Authentik login" ;;
esac
done

for public_path in /public/ /.cpr/w/browser.js; do
  public_status="$(curl --silent --show-error --max-time 8 --output /dev/null \
    --write-out '%{http_code}' --resolve copyparty.hchu.me:443:127.0.0.1 \
    "https://copyparty.hchu.me${public_path}")" || fail "Copyparty public read probe failed"
  [ "$public_status" = 200 ] || fail "Copyparty public read requires authentication"
done

for provider in headscale proxmox; do
oidc_config="$(curl --fail --silent --show-error --max-time 8 \
  --resolve auth.home.hchu.me:443:127.0.0.1 \
  "https://auth.home.hchu.me/application/o/${provider}/.well-known/openid-configuration")" \
  || fail "${provider} identity provider discovery failed"
printf '%s\n' "${oidc_config}" | python3 -c '
import json, sys
config = json.load(sys.stdin)
expected = "https://auth.home.hchu.me/application/o/" + sys.argv[1] + "/"
sys.exit(0 if config.get("issuer") == expected and config.get("jwks_uri") else 1)
' "$provider" || fail "${provider} identity provider discovery is invalid"
done

compose exec -T headscale headscale health --config /etc/headscale/config.yaml >/dev/null 2>&1 \
  || fail "Headscale control server is unhealthy"

printf 'homelab smoke passed\n'
