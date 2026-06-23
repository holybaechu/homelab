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

case "${mode}" in
  site)
    playbook="infra/ansible/playbooks/site.yml"
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
    ansible-playbook       -i "${inventory}"       "${repo_root}/${playbook}"       --limit "${limit}"       "$@"
  ) > "${log_dir}/${target}.log" 2>&1 &
  printf '%s %s
' "$!" "${target}" >> "${pid_file}"
done

failed=0

while read -r pid target; do
  if wait "${pid}"; then
    status="success"
  else
    status="failure"
    failed=1
  fi

  printf '::group::%s %s %s
' "${mode}" "${target}" "${status}"
  cat "${log_dir}/${target}.log"
  printf '::endgroup::
'
done < "${pid_file}"

exit "${failed}"
