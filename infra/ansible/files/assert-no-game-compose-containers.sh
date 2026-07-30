#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ ! -x "$1" ]; then
  echo "usage: $0 /absolute/path/to/docker" >&2
  exit 2
fi

docker_bin="$1"
case "${docker_bin}" in
  /*) ;;
  *) echo "Docker executable must be an absolute path" >&2; exit 2 ;;
esac

containers="$("${docker_bin}" ps -a --quiet \
  --filter label=com.docker.compose.project=game)"
if [ -n "${containers}" ]; then
  printf 'Retired game Compose containers still exist:\n%s\n' \
    "${containers}" >&2
  exit 1
fi
