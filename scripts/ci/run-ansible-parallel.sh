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
deployment_scope="${ANSIBLE_DEPLOYMENT_SCOPE:-full}"
openclaw_components="${OPENCLAW_COMPONENTS:-all}"

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

case "${deployment_scope}" in
  full)
    ;;
  arcane)
    TARGETS="docker_apps:svc_docker_apps"
    if [ "${mode}" = "site" ]; then
      echo "Arcane scope deploys workloads through Arcane; Ansible site is disabled" >&2
      exit 2
    fi
    ;;
  openclaw)
    case "${openclaw_components}" in
      gateway) TARGETS="openclaw:svc_openclaw" ;;
      all) TARGETS="ctf_executor:svc_ctf_executor openclaw:svc_openclaw" ;;
      *) echo "OPENCLAW_COMPONENTS must be gateway or all" >&2; exit 2 ;;
    esac
    ;;
  none)
    echo "No deployment targets were selected"
    exit 0
    ;;
  *)
    echo "ANSIBLE_DEPLOYMENT_SCOPE must be none, openclaw, arcane, or full" >&2
    exit 2
    ;;
esac

if [ "$mode" = "validate" ] && [ "${deployment_scope}" = "full" ]; then
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

if [ "${mode}" = "site" ] && [ "${deployment_scope}" = "openclaw" ]; then
  if [ "${openclaw_components}" = "gateway" ]; then
    run_foreground_target openclaw svc_openclaw --tags openclaw_local_docker,openclaw_native "$@"
    exit 0
  fi
  run_foreground_target ctf_executor svc_ctf_executor --tags ctf_executor "$@"
  run_foreground_target openclaw svc_openclaw --tags openclaw_local_docker,openclaw_native "$@"
  exit 0
fi

if [ "${mode}" = "site" ] && [ "${deployment_scope}" = "full" ]; then
  tailnet_entry=""
  ctf_executor_entry=""
  openclaw_entry=""
  docker_apps_entry=""
  pve_entry=""
  parallel_targets=""
  for entry in ${TARGETS}; do
    target="${entry%%:*}"
    case "${target}" in
      tailnet) tailnet_entry="${entry}" ;;
      ctf_executor) ctf_executor_entry="${entry}" ;;
      openclaw) openclaw_entry="${entry}" ;;
      docker_apps) docker_apps_entry="${entry}" ;;
      pve) pve_entry="${entry}" ;;
      *) parallel_targets="${parallel_targets} ${entry}" ;;
    esac
  done

  # Keep the retained executor configured until the local sandbox migration is
  # validated, but the Gateway now uses its own local Docker Engine.
  for required_entry in \
    "${tailnet_entry}" \
    "${ctf_executor_entry}" \
    "${openclaw_entry}" \
    "${docker_apps_entry}" \
    "${pve_entry}"; do
    if [ -z "${required_entry}" ]; then
      echo "The site deployment requires tailnet, ctf_executor, openclaw, docker_apps, and pve targets" >&2
      exit 2
    fi
  done

  target="${tailnet_entry%%:*}"
  limit="${tailnet_entry#*:}"
  if run_foreground_target "${target}" "${limit}" "$@"; then
    :
  else
    exit $?
  fi

  target="${ctf_executor_entry%%:*}"
  limit="${ctf_executor_entry#*:}"
  if run_foreground_target "${target}" "${limit}" --tags ctf_executor "$@"; then
    :
  else
    exit $?
  fi

  target="${openclaw_entry%%:*}"
  limit="${openclaw_entry#*:}"
  if run_foreground_target "${target}" "${limit}" --tags openclaw_local_docker,openclaw_native "$@"; then
    :
  else
    exit $?
  fi

  for required_entry in \
    "${docker_apps_entry}" \
    "${pve_entry}"; do
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
