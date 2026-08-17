#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
test_root="$(mktemp -d)"
fake_bin="${test_root}/bin"
tailnet_marker="${test_root}/tailnet-complete"
gateway_marker="${test_root}/openclaw-complete"
docker_marker="${test_root}/docker-complete"
pve_marker="${test_root}/pve-complete"
mkdir -p "${fake_bin}"

cleanup() {
  rm -rf "${test_root}"
}
trap cleanup EXIT

cat > "${fake_bin}/python3" <<'EOF'
#!/bin/sh
printf '%s\n' 'tailnet:svc_tailnet docker_apps:svc_docker_apps openclaw:svc_openclaw pve:pve_hosts'
EOF

cat > "${fake_bin}/ansible-playbook" <<'EOF'
#!/bin/sh
case "$*" in
  *'site.yml'*'--limit svc_tailnet'*)
    : > "${TAILNET_MARKER}"
    printf '%s\n' 'tailnet complete'
    ;;
  *'site.yml'*'--limit svc_openclaw'*'--tags openclaw_local_docker,openclaw_native'*)
    test -f "${TAILNET_MARKER}"
    : > "${GATEWAY_MARKER}"
    printf '%s\n' 'one Gateway complete'
    ;;
  *'site.yml'*'--limit svc_docker_apps'*)
    test -f "${GATEWAY_MARKER}"
    : > "${DOCKER_MARKER}"
    printf '%s\n' 'docker complete'
    ;;
  *'site.yml'*'--limit pve_hosts'*)
    test -f "${DOCKER_MARKER}"
    : > "${PVE_MARKER}"
    printf '%s\n' 'pve complete'
    ;;
  *'validate.yml'*'--limit svc_docker_apps'*)
    sleep "${FAKE_DELAY_SECONDS:-0}"
    printf '%s\n' 'docker validation complete'
    ;;
  *'validate.yml'*'--limit pve_hosts'*)
    printf '%s\n' 'pve validation complete'
    ;;
esac
EOF
chmod +x "${fake_bin}/python3" "${fake_bin}/ansible-playbook"

PATH="${fake_bin}:${PATH}" \
TAILNET_MARKER="${tailnet_marker}" \
GATEWAY_MARKER="${gateway_marker}" \
DOCKER_MARKER="${docker_marker}" \
PVE_MARKER="${pve_marker}" \
ANSIBLE_TARGET_TIMEOUT_SECONDS=10 \
  sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site

test -f "${pve_marker}"

set +e
fast_site_output="$(PATH="${fake_bin}:${PATH}" ANSIBLE_DEPLOYMENT_SCOPE=arcane sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site 2>&1)"
fast_site_status=$?
set -e
test "${fast_site_status}" -ne 0
printf '%s\n' "${fast_site_output}" | grep -F 'Arcane scope deploys workloads through Arcane; Ansible site is disabled'

rm -f "${gateway_marker}"
PATH="${fake_bin}:${PATH}" \
TAILNET_MARKER="${tailnet_marker}" \
GATEWAY_MARKER="${gateway_marker}" \
ANSIBLE_DEPLOYMENT_SCOPE=openclaw \
  sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" site
test -f "${gateway_marker}"

none_output="$(PATH="${fake_bin}:${PATH}" ANSIBLE_DEPLOYMENT_SCOPE=none sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" validate)"
test "${none_output}" = "No deployment targets were selected"

set +e
timeout_output="$(PATH="${fake_bin}:${PATH}" FAKE_DELAY_SECONDS=3 ANSIBLE_TARGET_TIMEOUT_SECONDS=1 sh "${repo_root}/scripts/ci/run-ansible-parallel.sh" validate 2>&1)"
timeout_status=$?
set -e
test "${timeout_status}" -ne 0
printf '%s\n' "${timeout_output}" | grep -F 'Ansible validate target docker_apps timed out after 1 seconds'
