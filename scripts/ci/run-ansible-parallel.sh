#!/bin/sh
set -eu

usage() {
  echo "usage: $0 site|validate [ansible-playbook args...]" >&2
}

if [ "$#" -lt 1 ]; then
  usage
  exit 2
fi

mode="$1"
shift

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
inventory="${repo_root}/infra/ansible/inventory/prod/hosts.yml"
TARGETS="$(python3 "${repo_root}/scripts/ci/render_ansible_targets.py")"
target_timeout_seconds="${ANSIBLE_TARGET_TIMEOUT_SECONDS:-1800}"

case "${target_timeout_seconds}" in
  ''|*[!0-9]*|0)
    echo "ANSIBLE_TARGET_TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac

case "${mode}" in
  site)
    playbook="infra/ansible/playbooks/site.yml"
    TARGETS="${TARGETS} pve:pve_hosts"
    ;;
  validate)
    playbook="infra/ansible/playbooks/validate.yml"
    ;;
  *)
    usage
    exit 2
    ;;
esac

if [ "$mode" = "validate" ]; then
  TARGETS="pve:pve_hosts ${TARGETS}"
fi

run_ansible_target() {
  target="$1"
  limit="$2"
  shift 2

  timeout \
    --signal=TERM \
    --kill-after=30s \
    "${target_timeout_seconds}s" \
    ansible-playbook \
      -i "${inventory}" \
      "${repo_root}/${playbook}" \
      --limit "${limit}" \
      "$@"
}

print_timeout_notice() {
  target="$1"
  printf 'Ansible %s target %s timed out after %s seconds\n' \
    "${mode}" "${target}" "${target_timeout_seconds}"
}

run_foreground_target() {
  target="$1"
  limit="$2"
  shift 2

  printf '::group::%s %s gate\n' "${mode}" "${target}"
  if run_ansible_target "${target}" "${limit}" "$@"; then
    result=0
  else
    result=$?
    if [ "${result}" -eq 124 ] || [ "${result}" -eq 137 ]; then
      print_timeout_notice "${target}"
    fi
  fi
  printf '::endgroup::\n'
  return "${result}"
}

if [ "${mode}" = "site" ]; then
  tailnet_entry=""
  docker_apps_entry=""
  pve_entry=""
  parallel_targets=""
  for entry in ${TARGETS}; do
    target="${entry%%:*}"
    case "${target}" in
      tailnet) tailnet_entry="${entry}" ;;
      docker_apps) docker_apps_entry="${entry}" ;;
      pve) pve_entry="${entry}" ;;
      *) parallel_targets="${parallel_targets} ${entry}" ;;
    esac
  done

  for required_entry in "${tailnet_entry}" "${docker_apps_entry}" "${pve_entry}"; do
    if [ -z "${required_entry}" ]; then
      echo "The site deployment requires tailnet, docker_apps, and pve targets" >&2
      exit 2
    fi
    target="${required_entry%%:*}"
    limit="${required_entry#*:}"
    if run_foreground_target "${target}" "${limit}" "$@"; then
      :
    else
      exit $?
    fi
  done
  TARGETS="${parallel_targets}"
fi

log_dir="$(mktemp -d)"
pid_file="${log_dir}/pids"
: > "${pid_file}"

cleanup() {
  rm -rf "${log_dir}"
}
trap cleanup EXIT

for entry in ${TARGETS}; do
  target="${entry%%:*}"
  limit="${entry#*:}"
  (
    run_ansible_target "${target}" "${limit}" "$@"
  ) > "${log_dir}/${target}.log" 2>&1 &
  printf '%s %s\n' "$!" "${target}" >> "${pid_file}"
done

failed=0

while read -r pid target; do
  if wait "${pid}"; then
    status="success"
    result=0
  else
    result=$?
    status="failure"
    failed=1
  fi

  printf '::group::%s %s %s\n' "${mode}" "${target}" "${status}"
  if [ "${result}" -eq 124 ] || [ "${result}" -eq 137 ]; then
    print_timeout_notice "${target}"
  fi
  cat "${log_dir}/${target}.log"
  printf '::endgroup::\n'
done < "${pid_file}"

exit "${failed}"
