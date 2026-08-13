#!/bin/sh
set -eu

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

[ "$#" -le 1 ] || die "usage: migrate-openclaw-native.sh [migrate|recover-only]"
migration_mode="${1:-migrate}"
case "${migration_mode}" in
  migrate|recover-only) ;;
  *) die "usage: migrate-openclaw-native.sh [migrate|recover-only]" ;;
esac

for migration_tool in ansible-playbook mkfifo ssh tar timeout; do
  command -v "${migration_tool}" >/dev/null 2>&1 \
    || die "required migration tool is unavailable: ${migration_tool}"
done

: "${DOCKER_APPS_SSH_HOST:?set DOCKER_APPS_SSH_HOST}"
: "${OPENCLAW_SSH_HOST:?set OPENCLAW_SSH_HOST}"
: "${OPENCLAW_EXPECTED_IP:?set OPENCLAW_EXPECTED_IP}"
: "${OPENCLAW_PROXY_IP:?set OPENCLAW_PROXY_IP}"
: "${OPENCLAW_CONTROL_UI_ORIGIN:?set OPENCLAW_CONTROL_UI_ORIGIN}"
: "${ANSIBLE_EXTRA_VARS_PATH:?set ANSIBLE_EXTRA_VARS_PATH}"
[ -r "${ANSIBLE_EXTRA_VARS_PATH}" ] || die "Ansible extra-vars file is unreadable"

case "${DOCKER_APPS_SSH_HOST}" in
  *[!0-9.]*|'') die "DOCKER_APPS_SSH_HOST must be an IPv4 address" ;;
esac
case "${OPENCLAW_SSH_HOST}" in
  *[!0-9.]*|'') die "OPENCLAW_SSH_HOST must be an IPv4 address" ;;
esac
[ "${OPENCLAW_SSH_HOST}" = "${OPENCLAW_EXPECTED_IP}" ] \
  || die "OpenClaw inventory address does not match the approved migration target"
[ "${DOCKER_APPS_SSH_HOST}" = "${OPENCLAW_PROXY_IP}" ] \
  || die "Docker application address does not match the approved proxy target"
[ "${DOCKER_APPS_SSH_HOST}" != "${OPENCLAW_SSH_HOST}" ] \
  || die "migration source and destination must be different hosts"
[ "${OPENCLAW_CONTROL_UI_ORIGIN}" = "https://openclaw.home.hchu.me" ] \
  || die "OpenClaw control UI origin differs from the approved HTTPS origin"
for migration_helper in \
  scripts/ci/openclaw-docker-failback.sh \
  scripts/ci/openclaw-native-watchdog.sh \
  scripts/ci/openclaw-tree-manifest.py \
  scripts/ci/prepare-openclaw-native-checkout.py; do
  [ -f "${migration_helper}" ] || die "migration helper is not a regular file: ${migration_helper}"
  [ ! -L "${migration_helper}" ] || die "migration helper must not be a symlink: ${migration_helper}"
  [ -r "${migration_helper}" ] || die "migration helper is unreadable: ${migration_helper}"
done

source_target="root@${DOCKER_APPS_SSH_HOST}"
destination_target="root@${OPENCLAW_SSH_HOST}"
source_setup=/opt/homelab-compose/openclaw-setup
source_runtime=/srv/homelab/docker-apps/openclaw
destination_setup=/home/openclaw/openclaw-setup
destination_state=/var/lib/openclaw
destination_auth=/home/openclaw/.config/openclaw
remote_prepare=/root/prepare-openclaw-native-checkout.py
remote_manifest=/root/openclaw-tree-manifest.py
remote_native_watchdog=/root/openclaw-native-watchdog.sh
remote_docker_failback=/root/openclaw-docker-failback.sh
destination_watchdog_root=/var/lib/openclaw-migration
destination_watchdog_marker=/var/lib/openclaw-migration/native-watchdog.armed
destination_watchdog_expired=/var/lib/openclaw-migration/native-watchdog.expired
destination_watchdog_deadline=/var/lib/openclaw-migration/native-watchdog.deadline
destination_setup_stage=/home/openclaw/.openclaw-setup.migration
destination_state_stage=/var/lib/.openclaw.migration
destination_auth_stage=/home/openclaw/.config/.openclaw.migration
destination_import_marker=/var/lib/.openclaw-native-migration-owned
destination_validated_marker=/var/lib/.openclaw-native-migration-validated
source_validated_marker=/opt/homelab-control/openclaw/native-cutover-validated
old_gateway_stopped=0
failback_armed=0
recoverable_state_detected=0
relay_destination_pid=""
relay_root="$(mktemp -d)"
case "${relay_root}" in
  /tmp/*|"${RUNNER_TEMP:-/tmp}"/*) ;;
  *) die "temporary relay directory is outside an approved temporary root" ;;
esac

run_ssh() {
  migration_host="$1"
  shift
  timeout --signal=TERM --kill-after=15s 900s ssh \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
    -i "${HOME}/.ssh/id_ed25519" \
    "${migration_host}" "$@"
}

prove_source_fence() {
  fence_phase="$1"
  run_ssh "${source_target}" "sh -s -- ${fence_phase}" <<'PROVE_SOURCE_FENCE'
set -eu
guard_root=/opt/homelab-control/openclaw/migration
armed="$guard_root/failback.armed"
deadline="$guard_root/failback.deadline"
boot_id="$guard_root/failback.boot-id"
marker_value=homelab-openclaw-native-migration-v1
for guard_file in "$armed" "$deadline" "$boot_id"; do
  test -f "$guard_file"
  test ! -L "$guard_file"
  test "$(stat -c '%u:%g %a' "$guard_file")" = "0:0 600"
done
test "$(wc -c < "$armed")" -eq 37
test "$(cat "$armed")" = "$marker_value"
guard_deadline="$(cat "$deadline")"
case "$guard_deadline" in ''|*[!0-9]*) exit 1 ;; esac
systemctl is-enabled --quiet openclaw-migration-failback.service
systemctl is-active --quiet openclaw-migration-failback.service
/usr/local/sbin/openclaw-migration-docker-failback once
test -f "$armed"
test ! -L "$armed"
test "$(stat -c '%u:%g %a' "$armed")" = "0:0 600"
test "$(wc -c < "$armed")" -eq 37
test "$(cat "$armed")" = "$marker_value"
systemctl is-active --quiet openclaw-migration-failback.service
cd /opt/homelab-compose/openclaw
test -z "$(docker compose ps --status running -q openclaw-gateway)"
! ss -H -ltn 'sport = :18789' \
  | grep -Eq '127\.0\.0\.1:18789([[:space:]]|$)'
PROVE_SOURCE_FENCE
}

relay_tar_stream() {
  source_command="$1"
  destination_command="$2"
  relay_fifo="${relay_root}/stream"
  mkfifo -m 0600 "${relay_fifo}"
  run_ssh "${destination_target}" "${destination_command}" < "${relay_fifo}" &
  relay_destination_pid=$!

  set +e
  timeout --signal=TERM --kill-after=15s 900s ssh \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
    -i "${HOME}/.ssh/id_ed25519" \
    "${source_target}" "${source_command}" > "${relay_fifo}"
  relay_source_result=$?
  wait "${relay_destination_pid}"
  relay_destination_result=$?
  set -e

  relay_destination_pid=""
  rm -f -- "${relay_fifo}"
  [ "${relay_source_result}" -eq 0 ] \
    || die "source tar stream failed"
  [ "${relay_destination_result}" -eq 0 ] \
    || die "destination tar extraction failed"
}

cleanup() {
  result=$?
  trap - EXIT HUP INT TERM
  set +e
  if [ -n "${relay_destination_pid}" ]; then
    kill "${relay_destination_pid}" 2>/dev/null || true
    wait "${relay_destination_pid}" 2>/dev/null || true
  fi
  rm -f -- "${relay_root}/stream"
  rmdir "${relay_root}" 2>/dev/null || true
  run_ssh "${destination_target}" \
    "rm -f -- '${remote_prepare}' '${remote_manifest}' '${remote_native_watchdog}' /run/openclaw-migration-gateway-token /run/openclaw-migration-probe.json /var/lib/.openclaw-native-migration-validated.tmp; rmdir /root/openclaw-migration-empty-hooks 2>/dev/null || true" \
    >/dev/null 2>&1
  run_ssh "${source_target}" \
    "rm -f -- '${remote_manifest}' /opt/homelab-control/openclaw/.native-cutover-validated.tmp; rmdir /run/openclaw-migration-empty-hooks 2>/dev/null || true" \
    >/dev/null 2>&1
  if [ "${result}" -ne 0 ] && { [ "${old_gateway_stopped}" -eq 1 ] || [ "${failback_armed}" -eq 1 ]; }; then
    printf '%s\n' "Migration failed; restarting the retained Docker Gateway." >&2
    # A rollback must first establish (or recover) a persistent source-side
    # state.  In the ordinary case this is a proven old-Gateway fence.  A
    # pre-existing force marker means an earlier cleanup already authorized
    # restore, so it is validated but never converted back into a fence.
    if ! timeout --signal=TERM --kill-after=15s 60s ssh \
      -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
      -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
      -i "${HOME}/.ssh/id_ed25519" "${source_target}" \
      "umask 077; tee '${remote_docker_failback}' >/dev/null; chmod 0700 '${remote_docker_failback}'" \
      < scripts/ci/openclaw-docker-failback.sh; then
      printf '%s\n' "CRITICAL: the persistent source failback helper could not be staged; native was not touched." >&2
      exit 75
    fi
    if rollback_state="$(
      run_ssh "${source_target}" "sh -s -- prepare-persistent-source-failback" <<'PREPARE_SOURCE_FAILBACK'
set -eu
guard_root=/opt/homelab-control/openclaw/migration
armed="$guard_root/failback.armed"
deadline="$guard_root/failback.deadline"
boot_id="$guard_root/failback.boot-id"
force="$guard_root/failback.force"
marker_value=homelab-openclaw-native-migration-v1
remote_helper=/root/openclaw-docker-failback.sh
installed_helper=/usr/local/sbin/openclaw-migration-docker-failback
unit=/etc/systemd/system/openclaw-migration-failback.service

validate_regular() {
  guard_path="$1"
  test -f "$guard_path"
  test ! -L "$guard_path"
  test "$(stat -c '%u:%g %a' "$guard_path")" = "0:0 600"
}
validate_owned_regular() {
  guard_path="$1"
  test -f "$guard_path"
  test ! -L "$guard_path"
  test "$(stat -c '%u:%g' "$guard_path")" = "0:0"
}
validate_marker() {
  validate_regular "$1"
  test "$(wc -c < "$1")" -eq 37
  test "$(cat "$1")" = "$marker_value"
}
validate_lease_files() {
  validate_regular "$deadline"
  guard_deadline="$(cat "$deadline")"
  case "$guard_deadline" in ''|*[!0-9]*) exit 64 ;; esac
  validate_regular "$boot_id"
  test -n "$(cat "$boot_id")"
}
atomic_guard_write() {
  guard_value="$1"
  guard_path="$2"
  guard_stage="$(mktemp "$guard_root/.failback.guard.XXXXXX")"
  printf '%s\n' "$guard_value" > "$guard_stage"
  chown root:root "$guard_stage"
  chmod 0600 "$guard_stage"
  mv -f -- "$guard_stage" "$guard_path"
  validate_regular "$guard_path"
}

if test -e "$guard_root" || test -L "$guard_root"; then
  test -d "$guard_root"
  test ! -L "$guard_root"
  test "$(stat -c '%u:%g %a' "$guard_root")" = "0:0 700"
else
  install -d -o root -g root -m 0700 "$guard_root"
fi
# Remove only transaction-stage residues created in this root-only directory.
# They are never commit points and will otherwise survive SIGKILL indefinitely.
for guard_stage in "$guard_root"/.failback.guard.* "$guard_root"/.failback.force.*; do
  if test -e "$guard_stage" || test -L "$guard_stage"; then
    validate_owned_regular "$guard_stage"
    rm -f -- "$guard_stage"
  fi
done
test -f "$remote_helper"
test ! -L "$remote_helper"
test "$(stat -c '%u:%g %a' "$remote_helper")" = "0:0 700"
helper_stage="$(mktemp /usr/local/sbin/.openclaw-migration-docker-failback.XXXXXX)"
install -o root -g root -m 0700 "$remote_helper" "$helper_stage"
mv -f -- "$helper_stage" "$installed_helper"
test "$(stat -c '%u:%g %a' "$installed_helper")" = "0:0 700"

unit_stage="$(mktemp /etc/systemd/system/.openclaw-migration-failback.XXXXXX)"
cat > "$unit_stage" <<'FAILBACK_UNIT'
[Unit]
Description=OpenClaw migration Docker failback guard
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service
ConditionPathExists=/opt/homelab-control/openclaw/migration/failback.armed

[Service]
Type=simple
ExecStart=/usr/local/sbin/openclaw-migration-docker-failback
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
FAILBACK_UNIT
chown root:root "$unit_stage"
chmod 0644 "$unit_stage"
mv -f -- "$unit_stage" "$unit"
test -f "$unit"
test ! -L "$unit"
test "$(stat -c '%u:%g %a' "$unit")" = "0:0 644"
systemctl daemon-reload >/dev/null

armed_present=0
force_present=0
if test -e "$armed" || test -L "$armed"; then
  validate_owned_regular "$armed"
  armed_present=1
fi
if test -e "$force" || test -L "$force"; then
  validate_marker "$force"
  force_present=1
fi

if test "$force_present" -eq 1; then
  # A force marker is an already-authorized restore.  Do not run the helper or
  # mutate its lease until the destination is persistently fenced again.  An
  # active durable retry is deliberately left running; an inactive one remains
  # enabled and is restarted immediately after destination proof.
  if test "$armed_present" -eq 1; then
    if test -e "$armed" || test -L "$armed"; then
      validate_marker "$armed"
    else
      # The running helper committed restore while state was sampled.  Force
      # remains sufficient authorization and is handled as force-only.
      armed_present=0
    fi
  fi
  validate_lease_files
  systemctl enable openclaw-migration-failback.service >/dev/null
  if test "$armed_present" -eq 1; then
    printf '%s\n' forced-pending
  else
    printf '%s\n' force-only
  fi
  exit 0
fi

# With no rollback authorization, every root-owned non-symlink prefix is safe
# to normalize while native remains untouched.  This recovers older armed-first
# transactions (including 0644 or truncated files) without accepting them as a
# committed fence.  Atomic root-only metadata and an enabled unit are complete
# before armed is replaced as the final commit point.
for guard_file in "$armed" "$deadline" "$boot_id"; do
  if test -e "$guard_file" || test -L "$guard_file"; then
    validate_owned_regular "$guard_file"
  fi
done
umask 077
atomic_guard_write "$(( $(date +%s) + 3000 ))" "$deadline"
atomic_guard_write "$(cat /proc/sys/kernel/random/boot_id)" "$boot_id"
systemctl enable openclaw-migration-failback.service >/dev/null
# Armed is the source-fence commit point and is installed last.
atomic_guard_write "$marker_value" "$armed"
# `start` is a no-op for an already-active valid fence and closes no quiesce
# window; it also resumes an inactive partial-prefix recovery.
systemctl start openclaw-migration-failback.service
systemctl is-enabled --quiet openclaw-migration-failback.service
systemctl is-active --quiet openclaw-migration-failback.service
validate_marker "$armed"
test ! -e "$force"
test ! -L "$force"
source_fence_proven=0
for attempt in $(seq 1 30); do
  cd /opt/homelab-compose/openclaw
  if test -z "$(docker compose ps --status running -q openclaw-gateway)" \
      && ! ss -H -ltn 'sport = :18789' \
        | grep -Eq '127\.0\.0\.1:18789([[:space:]]|$)'; then
    source_fence_proven=1
    break
  fi
  sleep 1
done
test "$source_fence_proven" -eq 1
systemctl is-active --quiet openclaw-migration-failback.service
printf '%s\n' fenced
PREPARE_SOURCE_FAILBACK
    )"; then
      :
    else
      printf '%s\n' "CRITICAL: a persistent source failback state could not be prepared and proven; native was not touched." >&2
      exit 75
    fi
    case "${rollback_state}" in
      fenced|forced-pending|force-only) ;;
      *)
        printf '%s\n' "CRITICAL: the source failback preparation returned an invalid state; native was not touched." >&2
        exit 75
        ;;
    esac

    destination_shutdown_proven=0
    if ! run_ssh "${destination_target}" "sh -s -- stop-native-for-failback" <<'STOP_NATIVE'
set -eu
systemctl mask --runtime --now openclaw-gateway.service
test "$(readlink /run/systemd/system/openclaw-gateway.service)" = /dev/null
# Runtime masks disappear on reboot. Remove only the exact persistent enable
# symlink, while native is already masked and stopped, before authorizing the
# other host to restore Docker. This closes the paired-valid recovery window.
if test -L /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service; then
  test "$(readlink -m /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service)" = \
    /etc/systemd/system/openclaw-gateway.service
  rm -f -- /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
fi
test ! -e /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
test ! -L /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
! systemctl is-enabled --quiet openclaw-gateway.service
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
STOP_NATIVE
    then
      printf '%s\n' "CRITICAL: native Gateway shutdown was not proven; persistent failback guards remain armed." >&2
    else
      destination_shutdown_proven=1
    fi
    [ "${destination_shutdown_proven}" -eq 1 ] || exit 72
    if ! run_ssh "${source_target}" "sh -s -- authorize-persistent-failback ${rollback_state}" <<'ROLLBACK'
set -eu
guard_root=/opt/homelab-control/openclaw/migration
armed="$guard_root/failback.armed"
deadline="$guard_root/failback.deadline"
boot_id="$guard_root/failback.boot-id"
force="$guard_root/failback.force"
marker_value=homelab-openclaw-native-migration-v1
compose_root=/opt/homelab-compose/openclaw

operation="$1"
requested_state="$2"
test "$operation" = authorize-persistent-failback
case "$requested_state" in
  fenced|forced-pending|force-only) ;;
  *) exit 64 ;;
esac
validate_regular() {
  guard_path="$1"
  test -f "$guard_path"
  test ! -L "$guard_path"
  test "$(stat -c '%u:%g %a' "$guard_path")" = "0:0 600"
}
validate_marker() {
  validate_regular "$1"
  test "$(wc -c < "$1")" -eq 37
  test "$(cat "$1")" = "$marker_value"
}
validate_lease_files() {
  validate_regular "$deadline"
  guard_deadline="$(cat "$deadline")"
  case "$guard_deadline" in ''|*[!0-9]*) exit 64 ;; esac
  validate_regular "$boot_id"
  test -n "$(cat "$boot_id")"
}
old_gateway_ready() {
  cd "$compose_root"
  docker compose ps --status running -q openclaw-gateway | grep -q . \
    && curl -fsS --connect-timeout 2 --max-time 5 \
      http://127.0.0.1:18789/readyz >/dev/null
}

test -x /usr/local/sbin/openclaw-migration-docker-failback
test "$(stat -c '%u:%g %a' /usr/local/sbin/openclaw-migration-docker-failback)" = "0:0 700"
test -f /etc/systemd/system/openclaw-migration-failback.service
test ! -L /etc/systemd/system/openclaw-migration-failback.service
validate_lease_files
# An already-forced helper may be crossing its armed-then-force unlink commit
# while state is inspected.  Native is now persistently disabled, so quiesce
# only these recovery cases and resume them deterministically below.  The
# ordinary fenced path stays continuously active and is never restarted.
if test "$requested_state" != fenced; then
  systemctl stop openclaw-migration-failback.service 2>/dev/null || true
fi

armed_present=0
force_present=0
if test -e "$armed" || test -L "$armed"; then
  validate_marker "$armed"
  armed_present=1
fi
if test -e "$force" || test -L "$force"; then
  validate_marker "$force"
  force_present=1
fi

case "$armed_present:$force_present" in
  0:0)
    # Only a previously forced helper can remove both markers. Its internal
    # commit is gated on old-Gateway readiness, which is re-proven here.
    test "$requested_state" != fenced
    old_gateway_ready
    ;;
  1:0)
    test "$requested_state" = fenced
    # Publish only the authorization marker.  The already-enabled, running
    # guard owns the retry; rewriting its lease or restarting it here would
    # introduce a second non-atomic state transition.
    force_stage="$(mktemp "$guard_root/.failback.force.XXXXXX")"
    printf '%s\n' "$marker_value" > "$force_stage"
    chown root:root "$force_stage"
    chmod 0600 "$force_stage"
    validate_marker "$force_stage"
    mv -f -- "$force_stage" "$force"
    ;;
  1:1)
    test "$requested_state" = forced-pending
    systemctl daemon-reload >/dev/null
    systemctl enable openclaw-migration-failback.service >/dev/null
    systemctl restart openclaw-migration-failback.service
    ;;
  0:1)
    test "$requested_state" != fenced
    if old_gateway_ready; then
      # Armed was the helper's restore commit point.  Readiness proves the
      # force-only residue is completed before its final marker is cleared.
      rm -f -- "$force"
    else
      # The committed restore is incomplete.  Recreate armed atomically from
      # the already-authorized force inode, then let the persistent helper own
      # all start retries.
      test ! -e "$armed"
      test ! -L "$armed"
      ln "$force" "$armed"
      test "$force" -ef "$armed"
      validate_marker "$armed"
      systemctl daemon-reload >/dev/null
      systemctl enable openclaw-migration-failback.service >/dev/null
      systemctl restart openclaw-migration-failback.service
    fi
    ;;
  *) exit 64 ;;
esac

for attempt in $(seq 1 90); do
  if old_gateway_ready; then
    test ! -e "$armed"
    test ! -L "$armed"
    test ! -e "$force"
    test ! -L "$force"
    exit 0
  fi
  sleep 2
done
exit 1
ROLLBACK
    then
      printf '%s\n' "CRITICAL: automatic Docker Gateway failback could not be verified." >&2
      exit 70
    fi
    if ! run_ssh "${source_target}" "sh -s -- disarm-rollback-failback" <<'DISARM_ROLLBACK_FAILBACK'
set -eu
if test -e /opt/homelab-control/openclaw/migration/failback.armed; then
  test -f /opt/homelab-control/openclaw/migration/failback.armed
  test "$(cat /opt/homelab-control/openclaw/migration/failback.armed)" = homelab-openclaw-native-migration-v1
fi
test ! -e /opt/homelab-control/openclaw/migration/failback.force
systemctl disable --now openclaw-migration-failback.service 2>/dev/null || true
! systemctl is-active --quiet openclaw-migration-failback.service
! systemctl is-enabled --quiet openclaw-migration-failback.service
cd /opt/homelab-compose/openclaw
docker compose ps --status running -q openclaw-gateway | grep -q .
curl -fsS http://127.0.0.1:18789/readyz >/dev/null
validated=/opt/homelab-control/openclaw/native-cutover-validated
if test -e "$validated"; then
  test -f "$validated"
  test ! -L "$validated"
  test "$(stat -c '%u:%g %a' "$validated")" = "0:0 600"
  test "$(cat "$validated")" = homelab-openclaw-native-migration-v1
  rm -f -- "$validated"
fi
rm -f \
  /opt/homelab-control/openclaw/migration/failback.armed \
  /opt/homelab-control/openclaw/migration/failback.deadline \
  /opt/homelab-control/openclaw/migration/failback.boot-id \
  /opt/homelab-control/openclaw/migration/failback.force
DISARM_ROLLBACK_FAILBACK
    then
      printf '%s\n' "CRITICAL: old Gateway is healthy but its failback guard could not be disarmed." >&2
      exit 73
    fi
    if ! run_ssh "${destination_target}" "sh -s -- disarm-rollback-native-watchdog" <<'DISARM_ROLLBACK_NATIVE'
set -eu
# Native has already been persistently disabled and the retained Docker
# Gateway is proven healthy.  Normalize any old armed-first transaction prefix
# only after structurally constraining it to the root-owned guard directory;
# marker contents were never a commit until the newer atomic armed-last flow.
guard_root=/var/lib/openclaw-migration
if test -e "$guard_root" || test -L "$guard_root"; then
  test -d "$guard_root"
  test ! -L "$guard_root"
  test "$(stat -c '%u:%g %a' "$guard_root")" = "0:0 700"
fi
for guard_path in \
  "$guard_root/native-watchdog.armed" \
  "$guard_root/native-watchdog.expired" \
  "$guard_root/native-watchdog.deadline" \
  "$guard_root/native-watchdog.boot-id" \
  "$guard_root"/.native-watchdog.guard.*; do
  if test -e "$guard_path" || test -L "$guard_path"; then
    test -f "$guard_path"
    test ! -L "$guard_path"
    test "$(stat -c '%u:%g' "$guard_path")" = "0:0"
  fi
done
# Make the persistent native service harmless before releasing either fence.
# A runtime mask may make disable --now return nonzero even though the wanted
# symlink is removed, so remove only that exact symlink and prove persistence.
if test -L /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service; then
  test "$(readlink -m /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service)" = \
    /etc/systemd/system/openclaw-gateway.service
  rm -f /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
fi
test ! -e /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
test ! -L /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
systemctl stop openclaw-gateway.service 2>/dev/null || true
! systemctl is-enabled --quiet openclaw-gateway.service
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
systemctl disable --now openclaw-migration-native-watchdog.service 2>/dev/null || true
! systemctl is-active --quiet openclaw-migration-native-watchdog.service
! systemctl is-enabled --quiet openclaw-migration-native-watchdog.service
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
validated=/var/lib/.openclaw-native-migration-validated
if test -e "$validated"; then
  test -f "$validated"
  test ! -L "$validated"
  test "$(stat -c '%u:%g %a' "$validated")" = "0:0 600"
  test "$(cat "$validated")" = homelab-openclaw-native-migration-v1
  rm -f -- "$validated"
fi
rm -f \
  /var/lib/openclaw-migration/native-watchdog.armed \
  /var/lib/openclaw-migration/native-watchdog.boot-id \
  /var/lib/openclaw-migration/native-watchdog.expired \
  /var/lib/openclaw-migration/native-watchdog.deadline
for guard_stage in "$guard_root"/.native-watchdog.guard.*; do
  if test -e "$guard_stage"; then
    rm -f -- "$guard_stage"
  fi
done
systemctl unmask --runtime openclaw-gateway.service
! systemctl is-enabled --quiet openclaw-gateway.service
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
DISARM_ROLLBACK_NATIVE
    then
      printf '%s\n' "CRITICAL: old Gateway is healthy but the native-stop guard could not be disarmed." >&2
      exit 74
    fi
    if ! run_ssh "${destination_target}" "sh -s -- cleanup-import" <<'CLEANUP_IMPORT'
set -eu
marker=/var/lib/.openclaw-native-migration-owned
marker_value=homelab-openclaw-native-migration-v1
if ! test -e "$marker" && ! test -L "$marker"; then
  exit 0
fi
test -f "$marker"
test ! -L "$marker"
test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600"
test "$(wc -c < "$marker")" -eq 37
test "$(cat "$marker")" = "$marker_value"
! systemctl is-active --quiet openclaw-gateway.service
for target in \
  /home/openclaw/.openclaw-setup.migration \
  /var/lib/.openclaw.migration \
  /home/openclaw/.config/.openclaw.migration \
  /home/openclaw/openclaw-setup \
  /var/lib/openclaw \
  /home/openclaw/.config/openclaw; do
  test "$(readlink -m -- "$target")" = "$target"
  rm -rf -- "$target"
done
install -d -o openclaw -g openclaw -m 0700 /var/lib/openclaw/workspace
install -d -o openclaw -g openclaw -m 0700 /home/openclaw/.config/openclaw
rm -f -- "$marker"
CLEANUP_IMPORT
    then
      printf '%s\n' "Destination import cleanup failed; the ownership marker was retained." >&2
      exit 71
    fi
    if [ "${migration_mode}" = recover-only ] \
        && [ "${recoverable_state_detected}" -eq 1 ] \
        && [ "${result}" -eq 1 ]; then
      printf '%s\n' "Interrupted OpenClaw migration recovered to the retained Docker Gateway." >&2
      result=0
    fi
  fi
  run_ssh "${source_target}" "rm -f -- '${remote_docker_failback}'" \
    >/dev/null 2>&1
  rm -f -- "${relay_root}/stream"
  rmdir "${relay_root}" 2>/dev/null || true
  exit "${result}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

destination_marker_state="$(run_ssh "${destination_target}" "sh -s -- marker-state" <<'DESTINATION_MARKER_STATE'
set -eu
marker=/var/lib/.openclaw-native-migration-validated
if test ! -e "$marker"; then
  printf '%s\n' absent
elif test -f "$marker" \
    && test ! -L "$marker" \
    && test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600" \
    && test "$(cat "$marker")" = homelab-openclaw-native-migration-v1; then
  printf '%s\n' valid
else
  printf '%s\n' invalid
fi
DESTINATION_MARKER_STATE
)"
source_marker_state="$(run_ssh "${source_target}" "sh -s -- marker-state" <<'SOURCE_MARKER_STATE'
set -eu
marker=/opt/homelab-control/openclaw/native-cutover-validated
if test ! -e "$marker"; then
  printf '%s\n' absent
elif test -f "$marker" \
    && test ! -L "$marker" \
    && test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600" \
    && test "$(cat "$marker")" = homelab-openclaw-native-migration-v1; then
  printf '%s\n' valid
else
  printf '%s\n' invalid
fi
SOURCE_MARKER_STATE
)"

if [ "${destination_marker_state}" = valid ] && [ "${source_marker_state}" = valid ]; then
  failback_armed=1
  old_gateway_stopped=1
  run_ssh "${destination_target}" "sh -s -- prove-recovered-native" <<'PROVE_RECOVERED_NATIVE'
set -eu
test ! -e /var/lib/openclaw-migration/native-watchdog.expired
systemctl is-enabled --quiet openclaw-gateway.service
systemctl is-active --quiet openclaw-gateway.service
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
PROVE_RECOVERED_NATIVE
  run_ssh "${source_target}" "sh -s -- prove-recovered-source" <<'PROVE_RECOVERED_SOURCE'
set -eu
cd /opt/homelab-compose/openclaw
test -z "$(docker compose ps --status running -q openclaw-gateway)"
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
curl -fsS --resolve openclaw.home.hchu.me:443:192.168.0.3 \
  https://openclaw.home.hchu.me/healthz >/dev/null
PROVE_RECOVERED_SOURCE
  ANSIBLE_DEPLOYMENT_SCOPE=full \
    ansible-playbook \
      -i infra/ansible/inventory/prod/hosts.yml \
      infra/ansible/playbooks/finalize-openclaw-native-cutover.yml \
      --extra-vars @"${ANSIBLE_EXTRA_VARS_PATH:?set ANSIBLE_EXTRA_VARS_PATH}"
  ANSIBLE_DEPLOYMENT_SCOPE=full \
    ansible-playbook \
      -i infra/ansible/inventory/prod/hosts.yml \
      infra/ansible/playbooks/validate.yml \
      --limit docker_apps
  run_ssh "${destination_target}" "sh -s -- finalize-validated-native" <<'FINALIZE_VALIDATED_NATIVE'
set -eu
test ! -e /var/lib/openclaw-migration/native-watchdog.expired
test "$(stat -c '%u:%g %a' /var/lib/.openclaw-native-migration-validated)" = "0:0 600"
test "$(cat /var/lib/.openclaw-native-migration-validated)" = homelab-openclaw-native-migration-v1
systemctl is-enabled --quiet openclaw-gateway.service
systemctl is-active --quiet openclaw-gateway.service
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
systemctl disable --now openclaw-migration-native-watchdog.service 2>/dev/null || true
! systemctl is-active --quiet openclaw-migration-native-watchdog.service
! systemctl is-enabled --quiet openclaw-migration-native-watchdog.service
rm -f \
  /var/lib/openclaw-migration/native-watchdog.armed \
  /var/lib/openclaw-migration/native-watchdog.boot-id \
  /var/lib/openclaw-migration/native-watchdog.deadline
systemctl unmask --runtime openclaw-gateway.service
systemctl is-active --quiet openclaw-gateway.service
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
FINALIZE_VALIDATED_NATIVE
  run_ssh "${source_target}" "sh -s -- finalize-validated-source" <<'FINALIZE_VALIDATED_SOURCE'
set -eu
test "$(stat -c '%u:%g %a' /opt/homelab-control/openclaw/native-cutover-validated)" = "0:0 600"
test "$(cat /opt/homelab-control/openclaw/native-cutover-validated)" = homelab-openclaw-native-migration-v1
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
systemctl disable --now openclaw-migration-failback.service 2>/dev/null || true
! systemctl is-active --quiet openclaw-migration-failback.service
! systemctl is-enabled --quiet openclaw-migration-failback.service
cd /opt/homelab-compose/openclaw
test -z "$(docker compose ps --status running -q openclaw-gateway)"
  rm -f \
    /opt/homelab-control/openclaw/migration/failback.armed \
    /opt/homelab-control/openclaw/migration/failback.deadline \
    /opt/homelab-control/openclaw/migration/failback.boot-id
FINALIZE_VALIDATED_SOURCE
  # Both persistent guards are now disarmed and proven inactive.  This is the
  # transaction commit point: rollback must no longer consume the import
  # ownership marker, while the durable validated-marker pair makes a failed
  # ownership release recoverable by the next manual run.
  old_gateway_stopped=0
  failback_armed=0
  run_ssh "${destination_target}" "sh -s -- release-import-ownership" <<'RELEASE_IMPORT_OWNERSHIP'
set -eu
marker=/var/lib/.openclaw-native-migration-owned
marker_value=homelab-openclaw-native-migration-v1
if test -e "$marker" || test -L "$marker"; then
  test -f "$marker"
  test ! -L "$marker"
  test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600"
  test "$(wc -c < "$marker")" -eq 37
  test "$(cat "$marker")" = "$marker_value"
  rm -f -- "$marker"
fi
RELEASE_IMPORT_OWNERSHIP
  rmdir "${relay_root}"
  trap - EXIT HUP INT TERM
  printf '%s\n' "The previously validated native OpenClaw cutover remains healthy."
  exit 0
fi

case "${destination_marker_state}:${source_marker_state}" in
  absent:absent) ;;
  *)
    failback_armed=1
    old_gateway_stopped=1
    recoverable_state_detected=1
    die "partial or invalid durable cutover markers require failback before reconciliation"
    ;;
esac

stale_migration_state="$(run_ssh "${destination_target}" "sh -s -- stale-state" <<'DESTINATION_STALE_STATE'
set -eu
if test -e /var/lib/openclaw-migration/native-watchdog.armed \
    || test -L /var/lib/openclaw-migration/native-watchdog.armed \
    || test -e /var/lib/openclaw-migration/native-watchdog.expired \
    || test -L /var/lib/openclaw-migration/native-watchdog.expired \
    || test -e /var/lib/openclaw-migration/native-watchdog.deadline \
    || test -L /var/lib/openclaw-migration/native-watchdog.deadline \
    || test -e /var/lib/openclaw-migration/native-watchdog.boot-id \
    || test -L /var/lib/openclaw-migration/native-watchdog.boot-id \
    || find /var/lib/openclaw-migration -maxdepth 1 \
      -name '.native-watchdog.guard.*' -print -quit 2>/dev/null | grep -q . \
    || test -e /var/lib/.openclaw-native-migration-owned \
    || test -L /var/lib/.openclaw-native-migration-owned \
    || systemctl is-active --quiet openclaw-migration-native-watchdog.service \
    || systemctl is-enabled --quiet openclaw-migration-native-watchdog.service \
    || systemctl is-active --quiet openclaw-gateway.service; then
  printf '%s\n' dirty
else
  printf '%s\n' clean
fi
DESTINATION_STALE_STATE
)"
source_stale_state="$(run_ssh "${source_target}" "sh -s -- stale-state" <<'SOURCE_STALE_STATE'
set -eu
if test -e /opt/homelab-control/openclaw/migration/failback.armed \
    || test -L /opt/homelab-control/openclaw/migration/failback.armed \
    || test -e /opt/homelab-control/openclaw/migration/failback.force \
    || test -L /opt/homelab-control/openclaw/migration/failback.force \
    || test -e /opt/homelab-control/openclaw/migration/failback.deadline \
    || test -L /opt/homelab-control/openclaw/migration/failback.deadline \
    || test -e /opt/homelab-control/openclaw/migration/failback.boot-id \
    || test -L /opt/homelab-control/openclaw/migration/failback.boot-id \
    || find /opt/homelab-control/openclaw/migration -maxdepth 1 \
      \( -name '.failback.guard.*' -o -name '.failback.force.*' \) \
      -print -quit 2>/dev/null | grep -q . \
    || systemctl is-active --quiet openclaw-migration-failback.service \
    || systemctl is-enabled --quiet openclaw-migration-failback.service; then
  printf '%s\n' dirty
else
  printf '%s\n' clean
fi
SOURCE_STALE_STATE
)"
if [ "${stale_migration_state}:${source_stale_state}" != clean:clean ]; then
  failback_armed=1
  old_gateway_stopped=1
  recoverable_state_detected=1
  die "stale migration state was detected; failback is required before reconciliation"
fi

if [ "${migration_mode}" = recover-only ]; then
  printf '%s\n' "OpenClaw migration recovery preflight is clean."
  exit 0
fi

for manifest_host in "${source_target}" "${destination_target}"; do
  timeout --signal=TERM --kill-after=15s 60s ssh \
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
    -i "${HOME}/.ssh/id_ed25519" "${manifest_host}" \
    "umask 077; tee '${remote_manifest}' >/dev/null; chmod 0700 '${remote_manifest}'" \
    < scripts/ci/openclaw-tree-manifest.py
done

run_ssh "${source_target}" "sh -s -- preflight" <<'SOURCE_PREFLIGHT'
set -eu
setup=/opt/homelab-compose/openclaw-setup
runtime=/srv/homelab/docker-apps/openclaw
command -v python3 >/dev/null 2>&1
hooks=/run/openclaw-migration-empty-hooks
install -d -o root -g root -m 0700 "$hooks"
test -z "$(find "$hooks" -mindepth 1 -print -quit)"
git_safe() {
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_PAGER=cat \
    git -c core.hooksPath="$hooks" -c core.fsmonitor=false "$@"
}
cd "$setup"
test ! -e "$setup/.gitmodules"
test -z "$(find "$setup" -path "$setup/.git" -prune -o -name .gitattributes -print -quit)"
test ! -e "$setup/.git/info/attributes"
test -z "$(find "$setup/.git" -type l -print -quit)"
test ! -e "$setup/.git/objects/info/alternates"
test ! -e "$setup/.git/objects/info/http-alternates"
set +e
git_config_names="$(git_safe config --local --name-only --get-regexp '.*')"
git_config_status=$?
set -e
test "$git_config_status" -eq 0 || test "$git_config_status" -eq 1
if printf '%s\n' "$git_config_names" | awk '
  {
    key=tolower($0)
    if (key ~ /^include/ || key == "extensions.worktreeconfig" ||
        key ~ /^core\.(fsmonitor|hookspath|attributesfile|worktree|editor|askpass|sshcommand|gitproxy|pager)$/ ||
        key == "sequence.editor" || key ~ /^credential\./ ||
        key == "diff.external" || key ~ /^diff\..*\.command$/ ||
        key ~ /^filter\..*\.(clean|smudge|process|required)$/ ||
        key ~ /^merge\..*\.driver$/ || key ~ /^gpg\..*\.program$/ ||
        key ~ /^remote\..*\.(promisor|partialclonefilter)$/ ||
        key ~ /^extensions\.partialclone$/ ||
        key ~ /^core\.alternaterefscommand$/) unsafe=1
  }
  END { exit !unsafe }
'; then
  exit 1
fi
unset git_config_names
remote_names="$(git_safe remote)"
test -z "$remote_names"
if git_safe ls-files --stage | awk '$1 == "160000" { found=1 } END { exit !found }'; then
  exit 1
fi
test "$(git_safe branch --show-current)" = main
git_safe rev-parse --verify HEAD >/dev/null
test -z "$(git_safe status --porcelain=v1)"
git_safe ls-files --error-unmatch config/openclaw.json >/dev/null
test -z "$(git_safe ls-files -- .env)"
test -f config/openclaw.json
test ! -L config/openclaw.json
test -d "$runtime/state"
test ! -L "$runtime/state"
plugin_skills="$runtime/state/plugin-skills"
test -d "$plugin_skills"
test ! -L "$plugin_skills"
test "$(stat -c '%u:%g %a' "$plugin_skills")" = "1000:1000 755"
test "$(find "$plugin_skills" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)" = \
  "$(printf '%s\n' browser-automation canvas)"
test -L "$plugin_skills/browser-automation"
test "$(stat -c '%u:%g %a' "$plugin_skills/browser-automation")" = "1000:1000 777"
test "$(readlink "$plugin_skills/browser-automation")" = \
  /app/dist/extensions/browser/skills/browser-automation
test -L "$plugin_skills/canvas"
test "$(stat -c '%u:%g %a' "$plugin_skills/canvas")" = "1000:1000 777"
test "$(readlink "$plugin_skills/canvas")" = \
  /app/dist/extensions/canvas/skills/canvas
test -d "$runtime/auth-profile-secrets"
test ! -L "$runtime/auth-profile-secrets"
test -z "$(find "$runtime/auth-profile-secrets" -mindepth 1 -print -quit)"
test -z "$(git_safe status --porcelain=v1 --ignored | grep '^!!' || true)"
test -n "$(docker compose -f /opt/homelab-compose/openclaw/compose.yml ps -q openclaw-gateway)"
docker compose -f /opt/homelab-compose/openclaw/compose.yml ps --status running -q openclaw-gateway | grep -q .
SOURCE_PREFLIGHT

run_ssh "${destination_target}" "sh -s -- preflight" <<'DESTINATION_PREFLIGHT'
set -eu
command -v python3 >/dev/null 2>&1
test "$(hostname)" = openclaw
test "$(id -u openclaw)" -eq 1000
test "$(id -g openclaw)" -eq 1000
test -x /opt/nodejs/current/bin/node
test -f /opt/openclaw/current/lib/node_modules/openclaw/openclaw.mjs
test ! -e /home/openclaw/openclaw-setup
test ! -e /home/openclaw/.openclaw-setup.migration
test -d /var/lib/openclaw
test ! -L /var/lib/openclaw
test -d /var/lib/openclaw/workspace
test -z "$(find /var/lib/openclaw -mindepth 1 -maxdepth 1 ! -name workspace -print -quit)"
test -z "$(find /var/lib/openclaw/workspace -mindepth 1 -print -quit)"
test ! -e /var/lib/.openclaw.migration
test -d /home/openclaw/.config/openclaw
test ! -L /home/openclaw/.config/openclaw
test -z "$(find /home/openclaw/.config/openclaw -mindepth 1 -print -quit)"
test ! -e /home/openclaw/.config/.openclaw.migration
test -s /etc/openclaw/secrets/gateway_token
test "$(stat -c '%u:%g %a' /etc/openclaw/secrets/gateway_token)" = "0:0 600"
test ! -e /run/openclaw-migration-gateway-token
test ! -e /run/openclaw-migration-probe.json
test ! -e /etc/systemd/system/multi-user.target.wants/openclaw-gateway.service
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
DESTINATION_PREFLIGHT

source_bytes="$(run_ssh "${source_target}" \
  "du -sb '${source_setup}' '${source_runtime}/state' '${source_runtime}/auth-profile-secrets' | awk '{total += \$1} END {print total}'")"
case "${source_bytes}" in
  ''|*[!0-9]*) die "source byte count was not a positive integer" ;;
esac
[ "${source_bytes}" -gt 0 ] || die "source byte count was zero"
destination_free="$(run_ssh "${destination_target}" "df -PB1 /var/lib/openclaw | awk 'NR == 2 {print \$4}'")"
case "${destination_free}" in
  ''|*[!0-9]*) die "destination free-space count was not an integer" ;;
esac
required_bytes=$((source_bytes * 2 + 67108864))
[ "${destination_free}" -ge "${required_bytes}" ] \
  || die "destination lacks the migration free-space safety margin"

printf '%s\n' "Stopping the old Gateway for the final state snapshot."
failback_armed=1
timeout --signal=TERM --kill-after=15s 60s ssh \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
  -i "${HOME}/.ssh/id_ed25519" "${destination_target}" \
  "umask 077; tee '${remote_native_watchdog}' >/dev/null; chmod 0700 '${remote_native_watchdog}'" \
  < scripts/ci/openclaw-native-watchdog.sh
timeout --signal=TERM --kill-after=15s 60s ssh \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
  -i "${HOME}/.ssh/id_ed25519" "${source_target}" \
  "umask 077; tee '${remote_docker_failback}' >/dev/null; chmod 0700 '${remote_docker_failback}'" \
  < scripts/ci/openclaw-docker-failback.sh
run_ssh "${destination_target}" "sh -s -- arm-native-watchdog" <<'ARM_NATIVE_WATCHDOG'
set -eu
guard_root=/var/lib/openclaw-migration
armed="$guard_root/native-watchdog.armed"
expired="$guard_root/native-watchdog.expired"
deadline_path="$guard_root/native-watchdog.deadline"
boot_id_path="$guard_root/native-watchdog.boot-id"
marker_value=homelab-openclaw-native-migration-v1
installed_helper=/usr/local/sbin/openclaw-migration-native-watchdog
unit=/etc/systemd/system/openclaw-migration-native-watchdog.service
if test -e "$guard_root" || test -L "$guard_root"; then
  test -d "$guard_root"
  test ! -L "$guard_root"
  test "$(stat -c '%u:%g %a' "$guard_root")" = "0:0 700"
else
  install -d -o root -g root -m 0700 "$guard_root"
fi
for guard_path in "$armed" "$expired" "$deadline_path" "$boot_id_path"; do
  test ! -e "$guard_path"
  test ! -L "$guard_path"
done
! systemctl is-active --quiet openclaw-migration-native-watchdog.service
! systemctl is-enabled --quiet openclaw-migration-native-watchdog.service
helper_stage="$(mktemp /usr/local/sbin/.openclaw-migration-native-watchdog.XXXXXX)"
install -o root -g root -m 0700 /root/openclaw-native-watchdog.sh "$helper_stage"
mv -f -- "$helper_stage" "$installed_helper"
test "$(stat -c '%u:%g %a' "$installed_helper")" = "0:0 700"
unit_stage="$(mktemp /etc/systemd/system/.openclaw-migration-native-watchdog.XXXXXX)"
cat > "$unit_stage" <<'WATCHDOG_UNIT'
[Unit]
Description=OpenClaw migration native safety fence
Before=openclaw-gateway.service
ConditionPathExists=/var/lib/openclaw-migration/native-watchdog.armed

[Service]
Type=simple
ExecStartPre=/usr/local/sbin/openclaw-migration-native-watchdog once
ExecStart=/usr/local/sbin/openclaw-migration-native-watchdog loop
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
WATCHDOG_UNIT
chown root:root "$unit_stage"
chmod 0644 "$unit_stage"
mv -f -- "$unit_stage" "$unit"
test -f "$unit"
test ! -L "$unit"
test "$(stat -c '%u:%g %a' "$unit")" = "0:0 644"
atomic_guard_write() {
  guard_value="$1"
  guard_path="$2"
  guard_stage="$(mktemp "$guard_root/.native-watchdog.guard.XXXXXX")"
  printf '%s\n' "$guard_value" > "$guard_stage"
  chown root:root "$guard_stage"
  chmod 0600 "$guard_stage"
  mv -f -- "$guard_stage" "$guard_path"
  test -f "$guard_path"
  test ! -L "$guard_path"
  test "$(stat -c '%u:%g %a' "$guard_path")" = "0:0 600"
}
umask 077
atomic_guard_write "$(( $(date +%s) + 2700 ))" "$deadline_path"
atomic_guard_write "$(cat /proc/sys/kernel/random/boot_id)" "$boot_id_path"
systemctl daemon-reload >/dev/null
systemctl enable openclaw-migration-native-watchdog.service >/dev/null
# Armed is the transaction commit and is published only after every persistent
# prerequisite is complete.
atomic_guard_write "$marker_value" "$armed"
systemctl start openclaw-migration-native-watchdog.service
systemctl is-enabled --quiet openclaw-migration-native-watchdog.service
systemctl is-active --quiet openclaw-migration-native-watchdog.service
"$installed_helper" once
test "$(cat "$armed")" = "$marker_value"
test ! -e "$expired"
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
ARM_NATIVE_WATCHDOG
run_ssh "${source_target}" "sh -s -- arm-failback" <<'ARM_FAILBACK'
set -eu
guard_root=/opt/homelab-control/openclaw/migration
armed="$guard_root/failback.armed"
force="$guard_root/failback.force"
deadline_path="$guard_root/failback.deadline"
boot_id_path="$guard_root/failback.boot-id"
marker_value=homelab-openclaw-native-migration-v1
installed_helper=/usr/local/sbin/openclaw-migration-docker-failback
unit=/etc/systemd/system/openclaw-migration-failback.service
if test -e "$guard_root" || test -L "$guard_root"; then
  test -d "$guard_root"
  test ! -L "$guard_root"
  test "$(stat -c '%u:%g %a' "$guard_root")" = "0:0 700"
else
  install -d -o root -g root -m 0700 "$guard_root"
fi
for guard_path in "$armed" "$force" "$deadline_path" "$boot_id_path"; do
  test ! -e "$guard_path"
  test ! -L "$guard_path"
done
! systemctl is-active --quiet openclaw-migration-failback.service
! systemctl is-enabled --quiet openclaw-migration-failback.service
helper_stage="$(mktemp /usr/local/sbin/.openclaw-migration-docker-failback.XXXXXX)"
install -o root -g root -m 0700 /root/openclaw-docker-failback.sh "$helper_stage"
mv -f -- "$helper_stage" "$installed_helper"
test "$(stat -c '%u:%g %a' "$installed_helper")" = "0:0 700"
unit_stage="$(mktemp /etc/systemd/system/.openclaw-migration-failback.XXXXXX)"
cat > "$unit_stage" <<'FAILBACK_UNIT'
[Unit]
Description=OpenClaw migration Docker failback guard
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service
ConditionPathExists=/opt/homelab-control/openclaw/migration/failback.armed

[Service]
Type=simple
ExecStart=/usr/local/sbin/openclaw-migration-docker-failback
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
FAILBACK_UNIT
chown root:root "$unit_stage"
chmod 0644 "$unit_stage"
mv -f -- "$unit_stage" "$unit"
test -f "$unit"
test ! -L "$unit"
test "$(stat -c '%u:%g %a' "$unit")" = "0:0 644"
atomic_guard_write() {
  guard_value="$1"
  guard_path="$2"
  guard_stage="$(mktemp "$guard_root/.failback.guard.XXXXXX")"
  printf '%s\n' "$guard_value" > "$guard_stage"
  chown root:root "$guard_stage"
  chmod 0600 "$guard_stage"
  mv -f -- "$guard_stage" "$guard_path"
  test -f "$guard_path"
  test ! -L "$guard_path"
  test "$(stat -c '%u:%g %a' "$guard_path")" = "0:0 600"
}
umask 077
atomic_guard_write "$(( $(date +%s) + 3000 ))" "$deadline_path"
atomic_guard_write "$(cat /proc/sys/kernel/random/boot_id)" "$boot_id_path"
systemctl daemon-reload >/dev/null
systemctl enable openclaw-migration-failback.service >/dev/null
# Armed is the transaction commit and is published only after every persistent
# prerequisite is complete.
atomic_guard_write "$marker_value" "$armed"
systemctl start openclaw-migration-failback.service
systemctl is-enabled --quiet openclaw-migration-failback.service
systemctl is-active --quiet openclaw-migration-failback.service
for attempt in $(seq 1 30); do
  cd /opt/homelab-compose/openclaw
  if test -z "$(docker compose ps --status running -q openclaw-gateway)" \
      && ! ss -H -ltn 'sport = :18789' \
        | grep -Eq '127\.0\.0\.1:18789([[:space:]]|$)'; then
    test "$(cat "$armed")" = "$marker_value"
    test ! -e "$force"
    exit 0
  fi
  sleep 1
done
exit 1
ARM_FAILBACK
old_gateway_stopped=1
run_ssh "${source_target}" \
  "cd /opt/homelab-compose/openclaw && docker compose stop -t 30 openclaw-gateway"
run_ssh "${source_target}" \
  "cd /opt/homelab-compose/openclaw && test -z \"\$(docker compose ps --status running -q openclaw-gateway)\""

source_setup_manifest="$(run_ssh "${source_target}" "python3 '${remote_manifest}' '${source_setup}'")"
source_state_manifest="$(run_ssh "${source_target}" \
  "python3 '${remote_manifest}' --exclude-docker-generated-plugin-skills '${source_runtime}/state'")"
source_auth_manifest="$(run_ssh "${source_target}" "python3 '${remote_manifest}' '${source_runtime}/auth-profile-secrets'")"

# The runner relays each tar stream through a FIFO. Hosts never trust each
# other and no archive containing private state or Git history is persisted.
run_ssh "${destination_target}" "sh -s -- create-import-ownership" <<'CREATE_IMPORT_OWNERSHIP'
set -eu
marker=/var/lib/.openclaw-native-migration-owned
marker_value=homelab-openclaw-native-migration-v1
test ! -e "$marker"
test ! -L "$marker"
umask 077
stage="$(mktemp /var/lib/.openclaw-native-migration-owned.tmp.XXXXXX)"
cleanup_stage() {
  if test -n "${stage:-}"; then
    rm -f -- "$stage"
  fi
}
trap cleanup_stage EXIT HUP INT TERM
printf '%s\n' "$marker_value" > "$stage"
chown root:root "$stage"
chmod 0600 "$stage"
test -f "$stage"
test ! -L "$stage"
test "$(stat -c '%u:%g %a' "$stage")" = "0:0 600"
test "$(wc -c < "$stage")" -eq 37
test "$(cat "$stage")" = "$marker_value"
ln -- "$stage" "$marker"
rm -f -- "$stage"
stage=
trap - EXIT HUP INT TERM
test -f "$marker"
test ! -L "$marker"
test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600"
test "$(wc -c < "$marker")" -eq 37
test "$(cat "$marker")" = "$marker_value"
CREATE_IMPORT_OWNERSHIP
relay_tar_stream \
  "tar --create --numeric-owner --one-file-system --directory='${source_setup}' ." \
  "umask 077; install -d -o root -g openclaw -m 0750 '${destination_setup_stage}'; tar --extract --no-same-owner --no-same-permissions --directory='${destination_setup_stage}'"

relay_tar_stream \
  "tar --create --numeric-owner --one-file-system --exclude='./plugin-skills' --directory='${source_runtime}/state' ." \
  "install -d -o openclaw -g openclaw -m 0700 '${destination_state_stage}'; tar --extract --no-same-owner --no-same-permissions --directory='${destination_state_stage}'; test ! -e '${destination_state_stage}/plugin-skills'; test ! -L '${destination_state_stage}/plugin-skills'; find '${destination_state_stage}' -xdev -exec chown -h openclaw:openclaw {} +; chmod 0700 '${destination_state_stage}' '${destination_state_stage}/workspace'"

relay_tar_stream \
  "tar --create --numeric-owner --one-file-system --directory='${source_runtime}/auth-profile-secrets' ." \
  "install -d -o openclaw -g openclaw -m 0700 '${destination_auth_stage}'; tar --extract --no-same-owner --no-same-permissions --directory='${destination_auth_stage}'; find '${destination_auth_stage}' -xdev -exec chown -h openclaw:openclaw {} +; chmod 0700 '${destination_auth_stage}'"

destination_setup_manifest="$(run_ssh "${destination_target}" "python3 '${remote_manifest}' '${destination_setup_stage}'")"
destination_state_manifest="$(run_ssh "${destination_target}" \
  "python3 '${remote_manifest}' --exclude-docker-generated-plugin-skills --allow-absent-docker-generated-plugin-skills '${destination_state_stage}'")"
destination_auth_manifest="$(run_ssh "${destination_target}" "python3 '${remote_manifest}' '${destination_auth_stage}'")"
[ "${destination_setup_manifest}" = "${source_setup_manifest}" ] \
  || die "private checkout transfer verification failed"
[ "${destination_state_manifest}" = "${source_state_manifest}" ] \
  || die "runtime state transfer verification failed"
[ "${destination_auth_manifest}" = "${source_auth_manifest}" ] \
  || die "auth-profile transfer verification failed"

run_ssh "${destination_target}" "sh -s -- promote" <<'PROMOTE'
set -eu
test -d /home/openclaw/.openclaw-setup.migration
test -d /var/lib/.openclaw.migration
test -d /home/openclaw/.config/.openclaw.migration
test -z "$(find /var/lib/openclaw -mindepth 1 -maxdepth 1 ! -name workspace -print -quit)"
test -z "$(find /var/lib/openclaw/workspace -mindepth 1 -print -quit)"
test -z "$(find /home/openclaw/.config/openclaw -mindepth 1 -print -quit)"
rmdir /var/lib/openclaw/workspace /var/lib/openclaw
rmdir /home/openclaw/.config/openclaw
mv /home/openclaw/.openclaw-setup.migration /home/openclaw/openclaw-setup
mv /var/lib/.openclaw.migration /var/lib/openclaw
mv /home/openclaw/.config/.openclaw.migration /home/openclaw/.config/openclaw
PROMOTE

# Copy only the public converter program; it contains no deployment secrets.
timeout --signal=TERM --kill-after=15s 60s ssh \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${HOME}/.ssh/known_hosts" \
  -i "${HOME}/.ssh/id_ed25519" \
  "${destination_target}" \
  "umask 077; tee '${remote_prepare}' >/dev/null" \
  < scripts/ci/prepare-openclaw-native-checkout.py
run_ssh "${destination_target}" "chmod 0700 '${remote_prepare}'"

run_ssh "${destination_target}" "sh -s -- prepare" <<'DESTINATION_PREPARE'
set -eu
openclaw_ip=192.168.0.5
proxy_ip=192.168.0.3
origin=https://openclaw.home.hchu.me
commit_name='Homelab Production Deployer'
commit_email='homelab-production-deployer@users.noreply.github.com'
setup=/home/openclaw/openclaw-setup
config="$setup/config/openclaw.json"
credential=/run/openclaw-migration-gateway-token
hooks=/root/openclaw-migration-empty-hooks
entrypoint=/opt/openclaw/current/lib/node_modules/openclaw/openclaw.mjs
node=/opt/nodejs/current/bin/node
export HOME=/home/openclaw
export PATH=/opt/nodejs/current/bin:/opt/openclaw/current/bin:/usr/local/bin:/usr/bin:/bin
export OPENCLAW_HOME=/home/openclaw
export OPENCLAW_STATE_DIR=/var/lib/openclaw
export OPENCLAW_CONFIG_PATH="$config"
export OPENCLAW_WORKSPACE_DIR=/var/lib/openclaw/workspace
export OPENCLAW_DISABLE_BONJOUR=1
export OPENCLAW_NO_AUTO_UPDATE=1
export OPENCLAW_NO_RESPAWN=1
export OPENCLAW_SERVICE_REPAIR_POLICY=external
export OPENCLAW_SUPERVISOR_MODE=external
install -d -o root -g root -m 0700 "$hooks"
test -z "$(find "$hooks" -mindepth 1 -print -quit)"
test -d "$setup/.git"
test ! -L "$setup/.git"
test ! -e "$setup/.gitmodules"
test -z "$(find "$setup/.git" -type l -print -quit)"
test ! -e "$setup/.git/objects/info/alternates"
test ! -e "$setup/.git/objects/info/http-alternates"
test -z "$(find "$setup" -path "$setup/.git" -prune -o -name .gitattributes -print -quit)"
test ! -e "$setup/.git/info/attributes"
git_safe() {
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_PAGER=cat \
    git -c core.hooksPath="$hooks" -c core.fsmonitor=false "$@"
}
cd "$setup"
set +e
git_config_names="$(git_safe config --local --name-only --get-regexp '.*')"
git_config_status=$?
set -e
test "$git_config_status" -eq 0 || test "$git_config_status" -eq 1
if printf '%s\n' "$git_config_names" | awk '
  {
    key=tolower($0)
    if (key ~ /^include/ || key == "extensions.worktreeconfig" ||
        key ~ /^core\.(fsmonitor|hookspath|attributesfile|worktree|editor|askpass|sshcommand|gitproxy|pager)$/ ||
        key == "sequence.editor" || key ~ /^credential\./ ||
        key == "diff.external" || key ~ /^diff\..*\.command$/ ||
        key ~ /^filter\..*\.(clean|smudge|process|required)$/ ||
        key ~ /^merge\..*\.driver$/ || key ~ /^gpg\..*\.program$/ ||
        key ~ /^remote\..*\.(promisor|partialclonefilter)$/ ||
        key ~ /^extensions\.partialclone$/ ||
        key ~ /^core\.alternaterefscommand$/) unsafe=1
  }
  END { exit !unsafe }
'; then
  exit 1
fi
unset git_config_names
remote_names="$(git_safe remote)"
test -z "$remote_names"
if git_safe ls-files --stage | awk '$1 == "160000" { found=1 } END { exit !found }'; then
  exit 1
fi

find "$setup" -xdev -exec chown -h root:openclaw {} +
find "$setup/.git" -xdev -exec chown -h root:root {} +
find "$setup" -xdev -path "$setup/.git" -prune -o -type d -exec chmod 0750 {} +
find "$setup" -xdev -path "$setup/.git" -prune -o -type f -exec chmod 0640 {} +
find "$setup/.git" -xdev -type d -exec chmod 0700 {} +
find "$setup/.git" -xdev -type f -exec chmod 0600 {} +
find /var/lib/openclaw -xdev -type d -exec chmod 0700 {} +
find /var/lib/openclaw -xdev -type f -exec chmod 0600 {} +
find /home/openclaw/.config/openclaw -xdev -type d -exec chmod 0700 {} +
find /home/openclaw/.config/openclaw -xdev -type f -exec chmod 0600 {} +
python3 /root/prepare-openclaw-native-checkout.py \
  --config "$config" \
  --readme "$setup/README.md" \
  --openclaw-ip "$openclaw_ip" \
  --proxy-ip "$proxy_ip" \
  --origin "$origin"
chown root:openclaw "$config" "$setup/README.md"
chmod 0640 "$config" "$setup/README.md"

git_safe diff --no-ext-diff --no-textconv --exit-code -- . ':!config/openclaw.json' ':!README.md'
git_safe diff --no-ext-diff --no-textconv --check -- config/openclaw.json README.md
git_safe diff --no-ext-diff --no-textconv --quiet -- config/openclaw.json && exit 1
git_safe diff --no-ext-diff --no-textconv --quiet -- README.md && exit 1
test ! -e "$credential"
install -o openclaw -g openclaw -m 0400 /etc/openclaw/secrets/gateway_token "$credential"
trap 'rm -f -- /run/openclaw-migration-gateway-token' EXIT HUP INT TERM
export OPENCLAW_GATEWAY_TOKEN_FILE="$credential"
sudo -u openclaw -H env \
  HOME="$HOME" PATH="$PATH" OPENCLAW_HOME="$OPENCLAW_HOME" \
  OPENCLAW_STATE_DIR="$OPENCLAW_STATE_DIR" OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG_PATH" \
  OPENCLAW_WORKSPACE_DIR="$OPENCLAW_WORKSPACE_DIR" OPENCLAW_DISABLE_BONJOUR=1 \
  OPENCLAW_NO_AUTO_UPDATE=1 OPENCLAW_NO_RESPAWN=1 OPENCLAW_SERVICE_REPAIR_POLICY=external \
  OPENCLAW_SUPERVISOR_MODE=external OPENCLAW_GATEWAY_TOKEN_FILE="$OPENCLAW_GATEWAY_TOKEN_FILE" \
  "$node" "$entrypoint" config validate --json >/dev/null
sudo -u openclaw -H env \
  HOME="$HOME" PATH="$PATH" OPENCLAW_HOME="$OPENCLAW_HOME" \
  OPENCLAW_STATE_DIR="$OPENCLAW_STATE_DIR" OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG_PATH" \
  OPENCLAW_WORKSPACE_DIR="$OPENCLAW_WORKSPACE_DIR" OPENCLAW_DISABLE_BONJOUR=1 \
  OPENCLAW_NO_AUTO_UPDATE=1 OPENCLAW_NO_RESPAWN=1 OPENCLAW_SERVICE_REPAIR_POLICY=external \
  OPENCLAW_SUPERVISOR_MODE=external OPENCLAW_GATEWAY_TOKEN_FILE="$OPENCLAW_GATEWAY_TOKEN_FILE" \
  "$node" "$entrypoint" secrets audit --check --json >/dev/null
rm -f -- "$credential"
trap - EXIT HUP INT TERM

git_safe add -- config/openclaw.json README.md
git_safe diff --cached --no-ext-diff --no-textconv --check
git_safe diff --cached --no-ext-diff --no-textconv --quiet && exit 1
test "$(git_safe diff --cached --no-ext-diff --no-textconv --name-only | LC_ALL=C sort)" = "$(printf '%s\n' README.md config/openclaw.json | LC_ALL=C sort)"
token_pattern=/etc/openclaw/secrets/gateway_token
test -f "$token_pattern"
test ! -L "$token_pattern"
test "$(stat -c '%u:%g %a' "$token_pattern")" = "0:0 600"
test "$(wc -c < "$token_pattern")" -eq 65
grep -Eq '^[0-9a-fA-F]{64}$' "$token_pattern"
set +e
git_safe grep --quiet -F -f "$token_pattern" --
tracked_token_status=$?
git_safe diff --cached --no-ext-diff --no-textconv --text | grep -Fq -f "$token_pattern"
cached_token_status=$?
set -e
test "$tracked_token_status" -eq 1
test "$cached_token_status" -eq 1
git_safe -c user.name="$commit_name" -c user.email="$commit_email" \
  commit --no-gpg-sign -m 'Migrate OpenClaw to native LXC' >/dev/null
test "$(git_safe branch --show-current)" = main
test -z "$(git_safe status --porcelain=v1)"
test "$(git_safe show --no-ext-diff --no-textconv --format= --name-only HEAD | LC_ALL=C sort)" = "$(printf '%s\n' README.md config/openclaw.json | LC_ALL=C sort)"
rmdir "$hooks"
DESTINATION_PREPARE

run_ssh "${destination_target}" "rm -f -- '${remote_prepare}' '${remote_manifest}'"
run_ssh "${source_target}" "rm -f -- '${remote_manifest}'"
printf '%s\n' "OpenClaw data import and audited private configuration commit completed."

printf '%s\n' "Activating and validating the native Gateway."
run_ssh "${destination_target}" "sh -s -- prove-native-lease" <<'PROVE_NATIVE_LEASE'
set -eu
test -f /var/lib/openclaw-migration/native-watchdog.armed
test "$(cat /var/lib/openclaw-migration/native-watchdog.armed)" = homelab-openclaw-native-migration-v1
test ! -e /var/lib/openclaw-migration/native-watchdog.expired
deadline="$(cat /var/lib/openclaw-migration/native-watchdog.deadline)"
case "$deadline" in ''|*[!0-9]*) exit 1 ;; esac
test "$(date +%s)" -lt "$deadline"
systemctl is-enabled --quiet openclaw-migration-native-watchdog.service
systemctl is-active --quiet openclaw-migration-native-watchdog.service
! systemctl is-active --quiet openclaw-gateway.service
! ss -H -ltn 'sport = :18789' | grep -q .
PROVE_NATIVE_LEASE
run_ssh "${source_target}" "sh -s -- prove-source-lease" <<'PROVE_SOURCE_LEASE'
set -eu
test -f /opt/homelab-control/openclaw/migration/failback.armed
test "$(cat /opt/homelab-control/openclaw/migration/failback.armed)" = homelab-openclaw-native-migration-v1
deadline="$(cat /opt/homelab-control/openclaw/migration/failback.deadline)"
case "$deadline" in ''|*[!0-9]*) exit 1 ;; esac
test "$(date +%s)" -lt "$deadline"
systemctl is-enabled --quiet openclaw-migration-failback.service
systemctl is-active --quiet openclaw-migration-failback.service
cd /opt/homelab-compose/openclaw
test -z "$(docker compose ps --status running -q openclaw-gateway)"
PROVE_SOURCE_LEASE
prove_source_fence prove-source-fence-before-activation
ANSIBLE_DEPLOYMENT_SCOPE=full \
  ansible-playbook \
    -i infra/ansible/inventory/prod/hosts.yml \
    infra/ansible/playbooks/site.yml \
    --limit openclaw \
    --extra-vars @"${ANSIBLE_EXTRA_VARS_PATH:?set ANSIBLE_EXTRA_VARS_PATH}" \
    --extra-vars openclaw_native_activate=true

ANSIBLE_DEPLOYMENT_SCOPE=full \
  ansible-playbook \
    -i infra/ansible/inventory/prod/hosts.yml \
    infra/ansible/playbooks/validate.yml \
    --limit openclaw \
    --extra-vars openclaw_native_activate=true

run_ssh "${destination_target}" "sh -s -- final-native-proof" <<'FINAL_NATIVE_PROOF'
set -eu
systemctl is-enabled --quiet openclaw-gateway.service
systemctl is-active --quiet openclaw-gateway.service
curl -fsS http://192.168.0.5:18789/healthz >/dev/null
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
plugin_skills=/var/lib/openclaw/plugin-skills
test -d "$plugin_skills"
test ! -L "$plugin_skills"
test "$(stat -c '%u:%g %a' "$plugin_skills")" = "1000:1000 700"
test "$(find "$plugin_skills" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)" = \
  "$(printf '%s\n' browser-automation canvas)"
for plugin_skill in browser-automation canvas; do
  plugin_link="$plugin_skills/$plugin_skill"
  test -L "$plugin_link"
  test "$(stat -c '%u:%g %a' "$plugin_link")" = "1000:1000 777"
  plugin_target="$(readlink -f "$plugin_link")"
  case "$plugin_target" in /opt/openclaw/*) ;; *) exit 1 ;; esac
  test -d "$plugin_target"
  test -f "$plugin_target/SKILL.md"
  test ! -L "$plugin_target/SKILL.md"
done
cd /home/openclaw/openclaw-setup
hooks=/root/openclaw-migration-empty-hooks
install -d -o root -g root -m 0700 "$hooks"
test -z "$(find "$hooks" -mindepth 1 -print -quit)"
test -z "$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_PAGER=cat \
  git -c core.hooksPath="$hooks" -c core.fsmonitor=false status --porcelain=v1)"
rmdir "$hooks"
FINAL_NATIVE_PROOF

run_ssh "${source_target}" "sh -s -- proxy-path-proof" <<'PROXY_PATH_PROOF'
set -eu
curl -fsS http://192.168.0.5:18789/healthz >/dev/null
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
curl -fsS --resolve openclaw.home.hchu.me:443:192.168.0.3 \
  https://openclaw.home.hchu.me/healthz >/dev/null
PROXY_PATH_PROOF

run_ssh "${destination_target}" "sh -s -- gateway-rpc-proof" <<'GATEWAY_RPC_PROOF'
set -eu
credential=/run/openclaw-migration-gateway-token
probe=/run/openclaw-migration-probe.json
test ! -e "$credential"
test ! -e "$probe"
install -o openclaw -g openclaw -m 0400 /etc/openclaw/secrets/gateway_token "$credential"
trap 'rm -f -- /run/openclaw-migration-gateway-token /run/openclaw-migration-probe.json' EXIT HUP INT TERM
sudo -u openclaw -H env \
  HOME=/home/openclaw \
  PATH=/opt/nodejs/current/bin:/opt/openclaw/current/bin:/usr/local/bin:/usr/bin:/bin \
  OPENCLAW_HOME=/home/openclaw \
  OPENCLAW_STATE_DIR=/var/lib/openclaw \
  OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json \
  OPENCLAW_WORKSPACE_DIR=/var/lib/openclaw/workspace \
  OPENCLAW_DISABLE_BONJOUR=1 \
  OPENCLAW_NO_AUTO_UPDATE=1 \
  OPENCLAW_NO_RESPAWN=1 \
  OPENCLAW_SERVICE_REPAIR_POLICY=external \
  OPENCLAW_SUPERVISOR_MODE=external \
  OPENCLAW_GATEWAY_TOKEN_FILE="$credential" \
  /opt/nodejs/current/bin/node \
  /opt/openclaw/current/lib/node_modules/openclaw/openclaw.mjs \
  gateway probe --url ws://192.168.0.5:18789 --json > "$probe"
python3 - "$probe" <<'PROBE_JSON'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload.get("ok") is True
assert payload.get("primaryTargetId") == "explicit"
targets = payload.get("targets")
assert isinstance(targets, list) and len(targets) == 1
assert targets[0].get("id") == "explicit"
assert targets[0].get("url") == "ws://192.168.0.5:18789"
assert targets[0].get("connect", {}).get("rpcOk") is True
PROBE_JSON
rm -f -- "$credential" "$probe"
trap - EXIT HUP INT TERM
GATEWAY_RPC_PROOF

prove_source_fence prove-source-fence-before-markers

# Atomic paired durable markers distinguish a fully validated transition from
# arbitrary active service state after a runner or host failure.
run_ssh "${destination_target}" "sh -s -- mark-validated-native" <<'MARK_VALIDATED_NATIVE'
set -eu
marker=/var/lib/.openclaw-native-migration-validated
stage=/var/lib/.openclaw-native-migration-validated.tmp
test ! -e "$marker"
test ! -e "$stage"
umask 077
printf '%s\n' homelab-openclaw-native-migration-v1 > "$stage"
chown root:root "$stage"
chmod 0600 "$stage"
mv -- "$stage" "$marker"
test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600"
test "$(cat "$marker")" = homelab-openclaw-native-migration-v1
MARK_VALIDATED_NATIVE
run_ssh "${source_target}" "sh -s -- mark-validated-source" <<'MARK_VALIDATED_SOURCE'
set -eu
marker=/opt/homelab-control/openclaw/native-cutover-validated
stage=/opt/homelab-control/openclaw/.native-cutover-validated.tmp
test ! -e "$marker"
test ! -e "$stage"
umask 077
printf '%s\n' homelab-openclaw-native-migration-v1 > "$stage"
chown root:root "$stage"
chmod 0600 "$stage"
mv -- "$stage" "$marker"
test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600"
test "$(cat "$marker")" = homelab-openclaw-native-migration-v1
cd /opt/homelab-compose/openclaw
test -z "$(docker compose ps --status running -q openclaw-gateway)"
MARK_VALIDATED_SOURCE

prove_source_fence prove-source-fence-before-finalizer

ANSIBLE_DEPLOYMENT_SCOPE=full \
  ansible-playbook \
    -i infra/ansible/inventory/prod/hosts.yml \
    infra/ansible/playbooks/finalize-openclaw-native-cutover.yml \
    --extra-vars @"${ANSIBLE_EXTRA_VARS_PATH:?set ANSIBLE_EXTRA_VARS_PATH}"
ANSIBLE_DEPLOYMENT_SCOPE=full \
  ansible-playbook \
    -i infra/ansible/inventory/prod/hosts.yml \
    infra/ansible/playbooks/validate.yml \
    --limit docker_apps

# Disarm the native-stop guard first, prove native remained healthy, and only
# then disarm source failback. This ordering cannot strand both services down.
run_ssh "${destination_target}" "sh -s -- disarm-native-watchdog" <<'DISARM_NATIVE_WATCHDOG'
set -eu
test "$(cat /var/lib/openclaw-migration/native-watchdog.armed)" = homelab-openclaw-native-migration-v1
test ! -e /var/lib/openclaw-migration/native-watchdog.expired
test "$(stat -c '%u:%g %a' /var/lib/.openclaw-native-migration-validated)" = "0:0 600"
test "$(cat /var/lib/.openclaw-native-migration-validated)" = homelab-openclaw-native-migration-v1
systemctl is-active --quiet openclaw-migration-native-watchdog.service
systemctl disable --now openclaw-migration-native-watchdog.service
! systemctl is-active --quiet openclaw-migration-native-watchdog.service
! systemctl is-enabled --quiet openclaw-migration-native-watchdog.service
test ! -e /var/lib/openclaw-migration/native-watchdog.expired
systemctl is-active --quiet openclaw-gateway.service
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
rm -f \
  /var/lib/openclaw-migration/native-watchdog.armed \
  /var/lib/openclaw-migration/native-watchdog.boot-id \
  /var/lib/openclaw-migration/native-watchdog.deadline
DISARM_NATIVE_WATCHDOG
run_ssh "${source_target}" "sh -s -- disarm-failback" <<'DISARM_FAILBACK'
set -eu
test "$(cat /opt/homelab-control/openclaw/migration/failback.armed)" = homelab-openclaw-native-migration-v1
test "$(stat -c '%u:%g %a' /opt/homelab-control/openclaw/native-cutover-validated)" = "0:0 600"
test "$(cat /opt/homelab-control/openclaw/native-cutover-validated)" = homelab-openclaw-native-migration-v1
curl -fsS http://192.168.0.5:18789/readyz >/dev/null
systemctl disable --now openclaw-migration-failback.service
! systemctl is-active --quiet openclaw-migration-failback.service
! systemctl is-enabled --quiet openclaw-migration-failback.service
rm -f \
  /opt/homelab-control/openclaw/migration/failback.armed \
  /opt/homelab-control/openclaw/migration/failback.deadline \
  /opt/homelab-control/openclaw/migration/failback.boot-id
cd /opt/homelab-compose/openclaw
test -z "$(docker compose ps --status running -q openclaw-gateway)"
DISARM_FAILBACK
# Both persistent guards are now disarmed and proven inactive.  Clear the
# rollback state before releasing import ownership so an interrupt cannot
# remove the durable marker pair while leaving an unowned imported tree.
old_gateway_stopped=0
failback_armed=0
run_ssh "${destination_target}" "sh -s -- release-import-ownership" <<'RELEASE_IMPORT_OWNERSHIP'
set -eu
marker=/var/lib/.openclaw-native-migration-owned
marker_value=homelab-openclaw-native-migration-v1
test -f "$marker"
test ! -L "$marker"
test "$(stat -c '%u:%g %a' "$marker")" = "0:0 600"
test "$(wc -c < "$marker")" -eq 37
test "$(cat "$marker")" = "$marker_value"
rm -f -- "$marker"
RELEASE_IMPORT_OWNERSHIP
rm -f -- "${relay_root}/stream"
rmdir "${relay_root}"
trap - EXIT
trap - HUP INT TERM
printf '%s\n' "Native OpenClaw is validated; the retained Docker Gateway is stopped."
