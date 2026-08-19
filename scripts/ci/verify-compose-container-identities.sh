#!/bin/sh
set -eu

default_projects="homelab"
identity_snapshot_tmp=""
identity_health_tmp=""
identity_current_tmp=""

cleanup() {
  for identity_cleanup_file in \
    "${identity_snapshot_tmp}" \
    "${identity_health_tmp}" \
    "${identity_current_tmp}"; do
    if [ -n "${identity_cleanup_file}" ]; then
      rm -f -- "${identity_cleanup_file}"
    fi
  done
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
  cat >&2 <<'EOF'
usage:
  verify-compose-container-identities.sh snapshot [PROJECT ...]
  verify-compose-container-identities.sh health [PROJECT ...]
  verify-compose-container-identities.sh verify BASELINE [PROJECT ...]

Run this read-only helper on the Docker Compose host. The default and only
application project is homelab. Transient Compose one-off containers are
intentionally excluded.
EOF
}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is required"
  docker info >/dev/null 2>&1 || die "the Docker daemon is unavailable"
}

validate_projects() {
  for identity_project in "$@"; do
    case "${identity_project}" in
      [A-Za-z0-9]* ) ;;
      * ) die "invalid Compose project name: ${identity_project}" ;;
    esac
    case "${identity_project}" in
      *[!A-Za-z0-9_.-]* ) die "invalid Compose project name: ${identity_project}" ;;
    esac
  done
}

is_oneoff_container() {
  identity_oneoff="$(
    docker inspect \
      --format '{{ index .Config.Labels "com.docker.compose.oneoff" }}' \
      "$1"
  )"
  case "${identity_oneoff}" in
    [Tt][Rr][Uu][Ee]) return 0 ;;
    *) return 1 ;;
  esac
}

list_project_containers() {
  docker ps --all --quiet \
    --filter "label=com.docker.compose.project=$1"
}

snapshot_projects() {
  identity_snapshot_tmp="$(mktemp "${TMPDIR:-/tmp}/homelab-compose-identities.XXXXXX")"

  identity_format='{{ index .Config.Labels "com.docker.compose.project" }}	{{ index .Config.Labels "com.docker.compose.service" }}	{{ .Name }}	{{ .Id }}	{{ .Created }}	{{ .State.StartedAt }}	{{ .RestartCount }}	{{ .Image }}	{{ .Config.Image }}'
  : > "${identity_snapshot_tmp}"

  for identity_project in "$@"; do
    identity_found=0
    for identity_container in $(list_project_containers "${identity_project}"); do
      if is_oneoff_container "${identity_container}"; then
        continue
      fi
      docker inspect --format "${identity_format}" "${identity_container}" \
        >> "${identity_snapshot_tmp}"
      identity_found=$((identity_found + 1))
    done
    if [ "${identity_found}" -eq 0 ]; then
      die "no long-lived containers found for Compose project ${identity_project}"
    fi
  done

  printf 'project\tservice\tname\tcontainer_id\tcreated\tstarted_at\trestart_count\timage_id\timage_ref\n'
  LC_ALL=C sort "${identity_snapshot_tmp}"
  rm -f -- "${identity_snapshot_tmp}"
  identity_snapshot_tmp=""
}

check_health() {
  identity_health_tmp="$(mktemp "${TMPDIR:-/tmp}/homelab-compose-health.XXXXXX")"
  : > "${identity_health_tmp}"
  identity_health_format='{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ .Name }}|{{ .State.Status }}|{{ if .State.Health }}{{ .State.Health.Status }}{{ else }}none{{ end }}'

  for identity_project in "$@"; do
    identity_found=0
    for identity_container in $(list_project_containers "${identity_project}"); do
      if is_oneoff_container "${identity_container}"; then
        continue
      fi
      identity_found=$((identity_found + 1))
      identity_line="$(
        docker inspect --format "${identity_health_format}" "${identity_container}"
      )"
      IFS='|' read -r identity_actual_project identity_service identity_name \
        identity_status identity_health <<EOF
${identity_line}
EOF
      if [ "${identity_status}" != "running" ]; then
        printf '%s/%s (%s) is %s\n' \
          "${identity_actual_project}" "${identity_service}" \
          "${identity_name}" "${identity_status}" >> "${identity_health_tmp}"
      elif [ "${identity_health}" != "none" ] \
        && [ "${identity_health}" != "healthy" ]; then
        printf '%s/%s (%s) health is %s\n' \
          "${identity_actual_project}" "${identity_service}" \
          "${identity_name}" "${identity_health}" >> "${identity_health_tmp}"
      fi
    done
    if [ "${identity_found}" -eq 0 ]; then
      printf 'no long-lived containers found for Compose project %s\n' \
        "${identity_project}" >> "${identity_health_tmp}"
    fi
  done

  if [ -s "${identity_health_tmp}" ]; then
    cat "${identity_health_tmp}" >&2
    exit 1
  fi
  printf 'All selected long-lived Compose containers are running and healthy.\n'
  rm -f -- "${identity_health_tmp}"
  identity_health_tmp=""
}

verify_snapshot() {
  identity_baseline="$1"
  shift
  [ -f "${identity_baseline}" ] \
    || die "baseline snapshot does not exist: ${identity_baseline}"
  identity_current_tmp="$(mktemp "${TMPDIR:-/tmp}/homelab-compose-current.XXXXXX")"
  snapshot_projects "$@" > "${identity_current_tmp}"

  if ! cmp -s "${identity_baseline}" "${identity_current_tmp}"; then
    if command -v diff >/dev/null 2>&1; then
      diff -u "${identity_baseline}" "${identity_current_tmp}" || true
    fi
    die "long-lived Compose container identities changed"
  fi
  check_health "$@"
  printf 'Long-lived Compose container identities match the baseline.\n'
  rm -f -- "${identity_current_tmp}"
  identity_current_tmp=""
}

mode="${1:-}"
case "${mode}" in
  snapshot|health)
    shift
    if [ "$#" -eq 0 ]; then
      set -- ${default_projects}
    fi
    validate_projects "$@"
    require_docker
    if [ "${mode}" = "snapshot" ]; then
      snapshot_projects "$@"
    else
      check_health "$@"
    fi
    ;;
  verify)
    [ "$#" -ge 2 ] || {
      usage
      exit 2
    }
    shift
    baseline="$1"
    shift
    if [ "$#" -eq 0 ]; then
      set -- ${default_projects}
    fi
    validate_projects "$@"
    require_docker
    verify_snapshot "${baseline}" "$@"
    ;;
  *)
    usage
    exit 2
    ;;
esac
