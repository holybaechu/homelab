#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
test_root="$(mktemp -d)"
fake_bin="${test_root}/bin"
guard="${repo_root}/infra/ansible/files/assert-no-game-compose-containers.sh"
mkdir -p "${fake_bin}" "${test_root}/game"

cleanup() {
  rm -rf "${test_root}"
}
trap cleanup EXIT

test ! -e "${test_root}/game/compose.yml"

cat > "${fake_bin}/docker" <<'EOF'
#!/bin/sh
test "$*" = \
  'ps -a --quiet --filter label=com.docker.compose.project=game' || exit 98
case "${FAKE_DOCKER_RESULT}" in
  survivor) printf '%s\n' 'deadbeef1234' ;;
  empty) ;;
  error) exit 77 ;;
  *) exit 99 ;;
esac
EOF
chmod +x "${fake_bin}/docker"

set +e
survivor_output="$({
  FAKE_DOCKER_RESULT=survivor sh "${guard}" "${fake_bin}/docker"
} 2>&1)"
survivor_status=$?
set -e
test "${survivor_status}" -ne 0
printf '%s\n' "${survivor_output}" | grep -F 'deadbeef1234'

FAKE_DOCKER_RESULT=empty sh "${guard}" "${fake_bin}/docker"

set +e
FAKE_DOCKER_RESULT=error sh "${guard}" "${fake_bin}/docker"
docker_error_status=$?
set -e
test "${docker_error_status}" -eq 77
