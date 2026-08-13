#!/bin/sh
set -eu

guard_root=/opt/homelab-control/openclaw/migration
armed="${guard_root}/failback.armed"
deadline_path="${guard_root}/failback.deadline"
boot_id_path="${guard_root}/failback.boot-id"
force_path="${guard_root}/failback.force"
marker_value=homelab-openclaw-native-migration-v1
compose_root=/opt/homelab-compose/openclaw

[ "$#" -le 1 ] || exit 64
failback_mode="${1:-loop}"
case "${failback_mode}" in
  loop|once) ;;
  *) exit 64 ;;
esac

validate_regular() {
  guard_path="$1"
  test -f "${guard_path}"
  test ! -L "${guard_path}"
  test "$(stat -c '%u:%g %a' "${guard_path}")" = "0:0 600"
}

stop_and_prove_old_gateway() {
  cd "${compose_root}"
  docker compose stop -t 30 openclaw-gateway >/dev/null
  test -z "$(docker compose ps --status running -q openclaw-gateway)"
  ! ss -H -ltn 'sport = :18789' \
    | grep -Eq '127\.0\.0\.1:18789([[:space:]]|$)'
}

start_and_prove_old_gateway() {
  cd "${compose_root}"
  docker compose start openclaw-gateway >/dev/null
  docker compose ps --status running -q openclaw-gateway | grep -q .
  curl -fsS --connect-timeout 2 --max-time 5 \
    http://127.0.0.1:18789/readyz >/dev/null
}

enforce_failback_guard() {
  validate_regular "${armed}"
  test "$(cat "${armed}")" = "${marker_value}"
  validate_regular "${deadline_path}"
  validate_regular "${boot_id_path}"
  armed_boot_id="$(cat "${boot_id_path}")"
  test -n "${armed_boot_id}"
  guard_deadline="$(cat "${deadline_path}")"
  case "${guard_deadline}" in
    ''|*[!0-9]*) exit 64 ;;
  esac

  # Only the migration orchestrator may request forced failback, and only
  # after it has runtime-masked and proven the native Gateway inactive.  The
  # marker survives runner loss and makes this service the persistent retry
  # owner instead of racing a second, foreground Compose start loop.
  if test -e "${force_path}" || test -L "${force_path}"; then
    validate_regular "${force_path}"
    test "$(cat "${force_path}")" = "${marker_value}"
    start_and_prove_old_gateway
    # Removing armed is the transaction commit: the service's loop and
    # ConditionPathExists can no longer re-enter fencing after Docker is ready.
    # A leftover force marker is harmless and is cleaned by recovery preflight.
    rm -f -- "${armed}"
    rm -f -- "${force_path}"
    return 0
  fi

  # No local deadline, reboot observation, or cross-host HTTP failure can prove
  # that native is persistently disabled.  Without the exact orchestrator force
  # marker above, this service has one behavior: continuously fence old.
  stop_and_prove_old_gateway
}

if test "${failback_mode}" = once; then
  test -e "${armed}" || test -L "${armed}"
  enforce_failback_guard
  exit 0
fi

while test -e "${armed}" || test -L "${armed}"; do
  enforce_failback_guard
  sleep 2
done
