#!/bin/sh
set -eu

guard_root=/var/lib/openclaw-migration
armed="${guard_root}/native-watchdog.armed"
deadline_path="${guard_root}/native-watchdog.deadline"
boot_id_path="${guard_root}/native-watchdog.boot-id"
expired="${guard_root}/native-watchdog.expired"
marker_value=homelab-openclaw-native-migration-v1

validate_regular() {
  guard_path="$1"
  test -f "${guard_path}"
  test ! -L "${guard_path}"
  test "$(stat -c '%u:%g %a' "${guard_path}")" = "0:0 600"
}

enforce_if_expired() {
  test -e "${armed}" || return 0
  validate_regular "${armed}"
  test "$(cat "${armed}")" = "${marker_value}"
  validate_regular "${deadline_path}"
  validate_regular "${boot_id_path}"
  armed_boot_id="$(cat "${boot_id_path}")"
  current_boot_id="$(cat /proc/sys/kernel/random/boot_id)"
  guard_deadline="$(cat "${deadline_path}")"
  case "${guard_deadline}" in
    ''|*[!0-9]*) exit 64 ;;
  esac
  if test "${current_boot_id}" != "${armed_boot_id}" \
      || test "$(date +%s)" -ge "${guard_deadline}"; then
    expired_stage="${guard_root}/.native-watchdog.expired.$$"
    trap 'rm -f -- "${expired_stage:-}"' EXIT HUP INT TERM
    umask 077
    printf '%s\n' "${marker_value}" > "${expired_stage}"
    chown root:root "${expired_stage}"
    chmod 0600 "${expired_stage}"
    mv -f -- "${expired_stage}" "${expired}"
    trap - EXIT HUP INT TERM
    systemctl mask --runtime --now openclaw-gateway.service >/dev/null
    test "$(readlink /run/systemd/system/openclaw-gateway.service)" = /dev/null
    # The runtime mask closes the current-boot race.  Remove only the exact
    # persistent enable symlink so an orchestrator-authorized recovery cannot
    # restore Docker before native is reboot-persistently disabled.
    native_enable=/etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
    if test -L "${native_enable}"; then
      test "$(readlink -m "${native_enable}")" = \
        /etc/systemd/system/openclaw-gateway.service
      rm -f -- "${native_enable}"
    fi
    test ! -e "${native_enable}"
    test ! -L "${native_enable}"
    ! systemctl is-enabled --quiet openclaw-gateway.service
    ! systemctl is-active --quiet openclaw-gateway.service
    ! ss -H -ltn 'sport = :18789' | grep -q .
  fi
}

case "${1:-loop}" in
  once)
    enforce_if_expired
    ;;
  loop)
    while test -e "${armed}"; do
      enforce_if_expired
      sleep 2
    done
    ;;
  *)
    exit 64
    ;;
esac
