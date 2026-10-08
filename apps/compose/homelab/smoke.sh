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

compose exec -T authentik-worker ak apply_blueprint /blueprints/homelab.yaml >/dev/null 2>&1 \
  || fail "identity blueprint application failed"

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

safe_search_enabled="$(
  awk '
    /^  safe_search:$/ { in_safe_search = 1; next }
    in_safe_search && /^    enabled:/ { print $2; exit }
    in_safe_search && /^  [^ ]/ { exit }
  ' generated/adguard/AdGuardHome.yaml
)"
[ "${safe_search_enabled}" = false ] || fail "AdGuard Safe Search is enabled"

host_ip="$(curl -4fsS https://api.ipify.org)"
qbittorrent_ip="$(
  compose exec -T qbittorrent sh -c '
    if command -v curl >/dev/null 2>&1; then
      exec curl -4fsS https://api.ipify.org
    fi
    exec wget -qO- https://api.ipify.org
  '
)"
[ -n "${host_ip}" ] && [ "${host_ip}" = "${qbittorrent_ip}" ] \
  || fail "qBittorrent does not use the host public address"

direct_listen_port="$(
  compose exec -T qbittorrent sh -c '
    if command -v curl >/dev/null 2>&1; then
      exec curl -fsS http://127.0.0.1:8080/api/v2/app/preferences
    fi
    exec wget -qO- http://127.0.0.1:8080/api/v2/app/preferences
  ' | python3 -c 'import json, sys; print(json.load(sys.stdin).get("listen_port", 0))'
)"
[ "${direct_listen_port}" = 35435 ] || fail "qBittorrent peer port is incorrect"
direct_container="$(compose ps -q qbittorrent)"
[ -n "$(docker port "${direct_container}" 35435/tcp)" ] \
  && [ -n "$(docker port "${direct_container}" 35435/udp)" ] \
  || fail "qBittorrent peer port is not published for TCP and UDP"

compose exec -T qbittorrent test -f /vuetorrent/public/index.html >/dev/null 2>&1 \
  || fail "VueTorrent assets are unavailable"
for setting in 'WebUI\AlternativeUIEnabled=true' 'WebUI\RootFolder=/vuetorrent'; do
  compose exec -T qbittorrent grep -Fx -- "$setting" \
    /config/qBittorrent/qBittorrent.conf >/dev/null 2>&1 \
    || fail "qBittorrent VueTorrent configuration is incorrect"
done
if compose exec -T qbittorrent grep -Fx -- 'Connection\Interface=tun0' \
  /config/qBittorrent/qBittorrent.conf >/dev/null 2>&1; then
  fail "qBittorrent is unexpectedly bound to tun0"
fi
effective_mods="$(compose exec -T qbittorrent printenv DOCKER_MODS 2>/dev/null)" \
  || fail "VueTorrent mod environment is unavailable"
printf '%s\n' "$effective_mods" | python3 -c '
import re, sys
value = sys.stdin.read().rstrip("\n")
sys.exit(0 if re.fullmatch(r"ghcr\.io/vuetorrent/vuetorrent-lsio-mod:[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}", value) else 1)
' || fail "VueTorrent mod must use an official version and exact digest"

printf 'homelab smoke passed\n'
