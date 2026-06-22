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
TARGETS="edge dns tailnet downloads files minecraft"

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

for target in ${TARGETS}; do
  (
    ansible-playbook \
      -i "${inventory}" \
      "${playbook}" \
      --limit "${target}" \
      "$@"
  ) > "${log_dir}/${target}.log" 2>&1 &
  printf '%s %s\n' "$!" "${target}" >> "${pid_file}"
done

failed=0

while read -r pid target; do
  if wait "${pid}"; then
    status="success"
  else
    status="failure"
    failed=1
  fi

  printf '::group::%s %s %s\n' "${mode}" "${target}" "${status}"
  cat "${log_dir}/${target}.log"
  printf '::endgroup::\n'
done < "${pid_file}"

exit "${failed}"
