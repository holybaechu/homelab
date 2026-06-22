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

inventory="infra/ansible/inventory/prod/hosts.yml"
TARGETS="edge:svc_edge dns:svc_dns tailnet:svc_tailnet downloads:svc_downloads files:svc_files minecraft:svc_minecraft hermes:svc_hermes"

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
    ansible-playbook       -i "${inventory}"       "${playbook}"       --limit "${limit}"       "$@"
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
