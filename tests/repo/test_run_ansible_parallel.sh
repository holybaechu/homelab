#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
test_root="$(mktemp -d)"
fake_bin="${test_root}/bin"
marker="${test_root}/tailnet-complete"
docker_marker="${test_root}/docker-retirement-complete"
mkdir -p "${fake_bin}"

cleanup() {
  rm -rf "${test_root}"
}
trap cleanup EXIT

cat > "${fake_bin}/python3" <<'EOF'
#!/bin/sh
printf '%s\n' 'tailnet:svc_tailnet docker_apps:svc_docker_apps'
EOF

cat > "${fake_bin}/ansible-playbook" <<'EOF'
#!/bin/sh
case "$*" in
  *'--limit svc_tailnet'*)
    printf '%s\n' 'tailnet started'
    sleep "${FAKE_TAILNET_DELAY_SECONDS:-1}"
    : > "${TAILNET_COMPLETE_MARKER}"
    printf '%s\n' 'tailnet complete'
    ;;
  *'--limit svc_docker_apps'*)
    if [ ! -f "${TAILNET_COMPLETE_MARKER}" ]; then
      printf '%s\n' 'docker started before tailnet completed' >&2
      exit 91
    fi
    sleep "${FAKE_DOCKER_DELAY_SECONDS:-0}"
    : > "${DOCKER_RETIREMENT_COMPLETE_MARKER}"
    printf '%s\n' 'docker started after tailnet'
    ;;
  *'site.yml'*'--limit pve_hosts'*)
    if [ ! -f "${DOCKER_RETIREMENT_COMPLETE_MARKER}" ]; then
      printf '%s\n' 'pve cleanup started before docker retirement completed' >&2
      exit 92
    fi
    printf '%s\n' 'pve cleanup started after docker retirement'
    ;;
  *'validate.yml'*'--limit pve_hosts'*)
    printf '%s\n' 'pve validation complete'
    ;;
esac
EOF

chmod +x "${fake_bin}/python3" "${fake_bin}/ansible-playbook"

PATH="${fake_bin}:${PATH}" \
TAILNET_COMPLETE_MARKER="${marker}" \
DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
ANSIBLE_TARGET_TIMEOUT_SECONDS=10 \
  sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site

rm -f "${marker}" "${docker_marker}"
set +e
timeout_output="$({
  PATH="${fake_bin}:${PATH}" \
  TAILNET_COMPLETE_MARKER="${marker}" \
  DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
  FAKE_TAILNET_DELAY_SECONDS=3 \
  ANSIBLE_TARGET_TIMEOUT_SECONDS=1 \
    sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site
} 2>&1)"
timeout_status=$?
set -e

printf '%s\n' "${timeout_output}"
test "${timeout_status}" -ne 0
printf '%s\n' "${timeout_output}" | grep -F 'timed out after 1 seconds'

: > "${marker}"
set +e
background_timeout_output="$({
  PATH="${fake_bin}:${PATH}" \
  TAILNET_COMPLETE_MARKER="${marker}" \
  DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
  FAKE_TAILNET_DELAY_SECONDS=0 \
  FAKE_DOCKER_DELAY_SECONDS=3 \
  ANSIBLE_TARGET_TIMEOUT_SECONDS=1 \
    sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" validate
} 2>&1)"
background_timeout_status=$?
set -e

printf '%s\n' "${background_timeout_output}"
test "${background_timeout_status}" -ne 0
printf '%s\n' "${background_timeout_output}" | grep -F \
  'Ansible validate target docker_apps timed out after 1 seconds'
printf '%s\n' "${background_timeout_output}" | grep -F \
  '::group::validate docker_apps failure'
