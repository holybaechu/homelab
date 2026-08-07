#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
test_root="$(mktemp -d)"
fake_bin="${test_root}/bin"
calls="${test_root}/docker-calls"
validator="${repo_root}/infra/ansible/files/validate-vuetorrent.sh"
mkdir -p "${fake_bin}"

cleanup() {
  rm -rf "${test_root}"
}
trap cleanup EXIT

cat > "${fake_bin}/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS}"

case "$*" in
  *"test -f /vuetorrent/public/index.html")
    test "${FAKE_DOCKER_SCENARIO}" != all_fail
    ;;
  *"AlternativeUIEnabled=true"*)
    test "${FAKE_DOCKER_SCENARIO}" != all_fail
    ;;
  *"RootFolder=/vuetorrent/public"*)
    test "${FAKE_DOCKER_SCENARIO}" != all_fail
    ;;
  *"exec -T qbittorrent grep -Fx -- Connection\\Interface=tun0"*)
    test "${FAKE_DOCKER_SCENARIO}" = all_fail
    ;;
  *"printenv DOCKER_MODS")
    case "${FAKE_DOCKER_SCENARIO}" in
      success)
        printf '%s\n' 'ghcr.io/vuetorrent/vuetorrent-lsio-mod:9.8.7'
        ;;
      command_fail)
        printf '%s\n' 'printenv-command-leak-z9Q7' >&2
        exit 77
        ;;
      *)
        printf '%s\n' \
          'ghcr.io/vuetorrent/vuetorrent-lsio-mod:9.8.7-extra::notice::mod-leak-z9Q7'
        ;;
    esac
    ;;
  "compose logs --no-color --tail 80 qbittorrent")
    if test "${FAKE_DOCKER_SCENARIO}" = command_fail; then
      printf '%s\n' 'logs-command-leak-z9Q7 ::error:: Bearer leaked-token' >&2
      exit 78
    fi
    printf '%s\n' 'VueTorrent docker mod initialization error'
    printf '%s\n' 'keyword-free-leak-z9Q7'
    printf '%s\n' 'apikey=api-leak-z9Q7'
    printf '%s\n' 'Authorization: Bearer bearer-leak-z9Q7'
    printf '%s\n' '::error file=container::workflow-command-leak-z9Q7'
    printf '\033[31mcontrol-sequence-leak-z9Q7\033[0m\n'
    printf '%s\n' 'WebUI Password_PBKDF2=password-leak-z9Q7'
    ;;
  *)
    exit 98
    ;;
esac
EOF
chmod +x "${fake_bin}/docker"

set +e
all_fail_output="$({
  FAKE_DOCKER_SCENARIO=all_fail \
  FAKE_DOCKER_CALLS="${calls}" \
  PATH="${fake_bin}:${PATH}" \
    sh "${validator}"
} 2>&1)"
all_fail_status=$?
set -e

test "${all_fail_status}" -eq 1
for service in qbittorrent; do
  printf '%s\n' "${all_fail_output}" | grep -F \
    "FAIL ${service} asset: /vuetorrent/public/index.html is missing or inaccessible"
  printf '%s\n' "${all_fail_output}" | grep -F \
    "FAIL ${service} config: exact WebUI\\AlternativeUIEnabled=true is missing"
  printf '%s\n' "${all_fail_output}" | grep -F \
    "FAIL ${service} config: exact WebUI\\RootFolder=/vuetorrent/public is missing"
  printf '%s\n' "${all_fail_output}" | grep -F \
    "FAIL ${service} environment: effective DOCKER_MODS=<invalid or unavailable; value suppressed>"
  printf '%s\n' "${all_fail_output}" | grep -F \
    "INFO ${service} init-log summary: VueTorrent mentioned=yes"
  printf '%s\n' "${all_fail_output}" | grep -F \
    "INFO ${service} init-log summary: Docker mod mentioned=yes"
  printf '%s\n' "${all_fail_output}" | grep -F \
    "INFO ${service} init-log summary: error/failure mentioned=yes"
done
printf '%s\n' "${all_fail_output}" | grep -F \
  'FAIL qbittorrent config: direct instance is unexpectedly bound to tun0'

for leaked_text in \
  'mod-leak-z9Q7' \
  'keyword-free-leak-z9Q7' \
  'api-leak-z9Q7' \
  'bearer-leak-z9Q7' \
  'workflow-command-leak-z9Q7' \
  'control-sequence-leak-z9Q7' \
  'password-leak-z9Q7'
do
  if printf '%s\n' "${all_fail_output}" | grep -F "${leaked_text}" >/dev/null; then
    printf 'container-controlled text leaked to output: %s\n' "${leaked_text}" >&2
    exit 1
  fi
done

for service in qbittorrent; do
  grep -F "exec -T ${service} test -f /vuetorrent/public/index.html" "${calls}"
  grep -F "exec -T ${service} printenv DOCKER_MODS" "${calls}"
  grep -Fx "compose logs --no-color --tail 80 ${service}" "${calls}"
done
grep -F 'AlternativeUIEnabled=true' "${calls}"
grep -F 'RootFolder=/vuetorrent/public' "${calls}"
grep -F 'Connection\Interface=tun0' "${calls}"

: > "${calls}"
success_output="$({
  FAKE_DOCKER_SCENARIO=success \
  FAKE_DOCKER_CALLS="${calls}" \
  PATH="${fake_bin}:${PATH}" \
    sh "${validator}"
} 2>&1)"

for service in qbittorrent; do
  printf '%s\n' "${success_output}" | grep -F \
    "PASS ${service} asset: /vuetorrent/public/index.html exists"
  printf '%s\n' "${success_output}" | grep -F \
    "PASS ${service} config: exact WebUI\\AlternativeUIEnabled=true"
  printf '%s\n' "${success_output}" | grep -F \
    "PASS ${service} config: exact WebUI\\RootFolder=/vuetorrent/public"
  printf '%s\n' "${success_output}" | grep -F \
    "PASS ${service} environment: effective DOCKER_MODS=ghcr.io/vuetorrent/vuetorrent-lsio-mod:9.8.7"
done
printf '%s\n' "${success_output}" | grep -F \
  'PASS qbittorrent config: direct instance is not bound to tun0'
if grep -F 'compose logs' "${calls}" >/dev/null; then
  printf '%s\n' 'successful validation unexpectedly requested container logs' >&2
  exit 1
fi

: > "${calls}"
set +e
command_fail_output="$({
  FAKE_DOCKER_SCENARIO=command_fail \
  FAKE_DOCKER_CALLS="${calls}" \
  PATH="${fake_bin}:${PATH}" \
    sh "${validator}"
} 2>&1)"
command_fail_status=$?
set -e

test "${command_fail_status}" -eq 1
for service in qbittorrent; do
  printf '%s\n' "${command_fail_output}" | grep -F \
    "FAIL ${service} environment: effective DOCKER_MODS=<invalid or unavailable; value suppressed>; printenv command failed"
  printf '%s\n' "${command_fail_output}" | grep -F \
    "INFO ${service} init-log summary unavailable (docker compose logs command failed)"
done
for leaked_text in \
  'printenv-command-leak-z9Q7' \
  'logs-command-leak-z9Q7' \
  'Bearer leaked-token' \
  '::error::'
do
  if printf '%s\n' "${command_fail_output}" | grep -F "${leaked_text}" >/dev/null; then
    printf 'failed-command text leaked to output: %s\n' "${leaked_text}" >&2
    exit 1
  fi
done
