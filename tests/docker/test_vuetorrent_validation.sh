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
  *"test -f /vuetorrent/index.html")
    test "${FAKE_DOCKER_SCENARIO}" = success
    ;;
  *"AlternativeUIEnabled=true"*)
    test "${FAKE_DOCKER_SCENARIO}" = success
    ;;
  *"RootFolder=/vuetorrent"*)
    test "${FAKE_DOCKER_SCENARIO}" = success
    ;;
  *"printenv DOCKER_MODS")
    if test "${FAKE_DOCKER_SCENARIO}" = success; then
      printf '%s\n' 'ghcr.io/vuetorrent/vuetorrent-lsio-mod:9.8.7'
    else
      printf '%s\n' 'unexpected-mod'
    fi
    ;;
  "compose logs --no-color --tail 80 qbittorrent")
    printf '%s\n' 'normal init line'
    printf '%s\n' 'WebUI Password_PBKDF2=very-secret-hash'
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
printf '%s\n' "${all_fail_output}" | grep -F \
  'FAIL asset: /vuetorrent/index.html is missing or inaccessible'
printf '%s\n' "${all_fail_output}" | grep -F \
  'FAIL config: exact WebUI\AlternativeUIEnabled=true is missing'
printf '%s\n' "${all_fail_output}" | grep -F \
  'FAIL config: exact WebUI\RootFolder=/vuetorrent is missing'
printf '%s\n' "${all_fail_output}" | grep -F \
  'FAIL environment: effective DOCKER_MODS=unexpected-mod'
printf '%s\n' "${all_fail_output}" | grep -F 'normal init line'
printf '%s\n' "${all_fail_output}" | grep -F \
  '[redacted potentially sensitive init-log line]'
if printf '%s\n' "${all_fail_output}" | grep -F 'very-secret-hash' >/dev/null; then
  printf '%s\n' 'sensitive init-log content was not redacted' >&2
  exit 1
fi

grep -F 'test -f /vuetorrent/index.html' "${calls}"
grep -F 'AlternativeUIEnabled=true' "${calls}"
grep -F 'RootFolder=/vuetorrent' "${calls}"
grep -F 'printenv DOCKER_MODS' "${calls}"
grep -Fx 'compose logs --no-color --tail 80 qbittorrent' "${calls}"

: > "${calls}"
success_output="$({
  FAKE_DOCKER_SCENARIO=success \
  FAKE_DOCKER_CALLS="${calls}" \
  PATH="${fake_bin}:${PATH}" \
    sh "${validator}"
} 2>&1)"

printf '%s\n' "${success_output}" | grep -F \
  'PASS asset: /vuetorrent/index.html exists'
printf '%s\n' "${success_output}" | grep -F \
  'PASS config: exact WebUI\AlternativeUIEnabled=true'
printf '%s\n' "${success_output}" | grep -F \
  'PASS config: exact WebUI\RootFolder=/vuetorrent'
printf '%s\n' "${success_output}" | grep -F \
  'PASS environment: effective DOCKER_MODS=ghcr.io/vuetorrent/vuetorrent-lsio-mod:9.8.7'
if grep -F 'compose logs' "${calls}" >/dev/null; then
  printf '%s\n' 'successful validation unexpectedly requested container logs' >&2
  exit 1
fi
