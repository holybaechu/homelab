#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
test_root="$(mktemp -d)"
fake_bin="${test_root}/bin"
marker="${test_root}/tailnet-complete"
docker_marker="${test_root}/docker-retirement-complete"
pve_marker="${test_root}/pve-cleanup-started"
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
  *'validate.yml'*'--limit svc_docker_apps'*)
    sleep "${FAKE_DOCKER_DELAY_SECONDS:-0}"
    printf '%s\n' 'docker validation complete'
    ;;
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
    if [ "${FAKE_DOCKER_FAIL:-0}" = "1" ]; then
      printf '%s\n' 'docker retirement failed deliberately' >&2
      exit 93
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
    : > "${PVE_CLEANUP_STARTED_MARKER}"
    printf '%s\n' 'pve cleanup started after docker retirement'
    ;;
  *'validate.yml'*'--limit pve_hosts'*)
    printf '%s\n' 'pve validation complete'
    ;;
esac
EOF

chmod +x "${fake_bin}/python3" "${fake_bin}/ansible-playbook"

set +e
fast_site_output="$({
  PATH="${fake_bin}:${PATH}" \
  ANSIBLE_DEPLOYMENT_SCOPE=arcane \
  ANSIBLE_TARGET_TIMEOUT_SECONDS=10 \
    sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site
} 2>&1)"
fast_site_status=$?
set -e
printf '%s\n' "${fast_site_output}"
test "${fast_site_status}" -ne 0
printf '%s\n' "${fast_site_output}" | grep -F \
  'Arcane scope deploys workloads through Arcane; Ansible site is disabled'

fast_path_output="$(
  PATH="${fake_bin}:${PATH}" \
  ANSIBLE_DEPLOYMENT_SCOPE=arcane \
  TAILNET_COMPLETE_MARKER="${marker}" \
  DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
  PVE_CLEANUP_STARTED_MARKER="${pve_marker}" \
  ANSIBLE_TARGET_TIMEOUT_SECONDS=10 \
    sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" validate
)"
printf '%s\n' "${fast_path_output}"
printf '%s\n' "${fast_path_output}" | grep -F '::group::validate docker_apps success'
if printf '%s\n' "${fast_path_output}" | grep -F 'pve validation complete'; then
  exit 1
fi

PATH="${fake_bin}:${PATH}" \
TAILNET_COMPLETE_MARKER="${marker}" \
DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
PVE_CLEANUP_STARTED_MARKER="${pve_marker}" \
ANSIBLE_TARGET_TIMEOUT_SECONDS=10 \
  sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site

rm -f "${marker}" "${docker_marker}" "${pve_marker}"
set +e
docker_failure_output="$({
  PATH="${fake_bin}:${PATH}" \
  TAILNET_COMPLETE_MARKER="${marker}" \
  DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
  PVE_CLEANUP_STARTED_MARKER="${pve_marker}" \
  FAKE_DOCKER_FAIL=1 \
  ANSIBLE_TARGET_TIMEOUT_SECONDS=10 \
    sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site
} 2>&1)"
docker_failure_status=$?
set -e

printf '%s\n' "${docker_failure_output}"
test "${docker_failure_status}" -ne 0
printf '%s\n' "${docker_failure_output}" | grep -F \
  'docker retirement failed deliberately'
test ! -e "${pve_marker}"
if printf '%s\n' "${docker_failure_output}" | grep -F \
  'pve cleanup started after docker retirement'; then
  exit 1
fi

rm -f "${marker}" "${docker_marker}"
set +e
timeout_output="$({
  PATH="${fake_bin}:${PATH}" \
  TAILNET_COMPLETE_MARKER="${marker}" \
  DOCKER_RETIREMENT_COMPLETE_MARKER="${docker_marker}" \
  PVE_CLEANUP_STARTED_MARKER="${pve_marker}" \
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
  PVE_CLEANUP_STARTED_MARKER="${pve_marker}" \
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
