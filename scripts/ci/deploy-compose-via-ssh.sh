#!/bin/sh
set -eu
operation=${1:?set operation}
bundle=${2:?set private bundle path}
: "${DOCKER_APPS_HOST:?resolve DOCKER_APPS_HOST from topology}"
: "${GITHUB_SHA:?set exact source revision}"
case "$operation" in deploy|sync-secrets) ;; *) exit 2 ;; esac
remote="root@$DOCKER_APPS_HOST"
options="-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15"
temporary=$(ssh $options "$remote" 'umask 077; mktemp -d /tmp/homelab-deploy.XXXXXXXX')
case "$temporary" in /tmp/homelab-deploy.*) ;; *) exit 1 ;; esac
cleanup() { ssh $options "$remote" "rm -rf -- '$temporary'" || true; }
trap cleanup EXIT HUP INT TERM
scp $options "$bundle" "$remote:$temporary/apps.json"
if [ "$operation" = deploy ]; then
  # Only tracked public package files are transported.
  mkdir -p "$RUNNER_TEMP/homelab-source"
  git archive "$GITHUB_SHA" apps/compose/homelab | tar -x -C "$RUNNER_TEMP/homelab-source"
  source="$RUNNER_TEMP/homelab-source/apps/compose/homelab"
  cp infra/ansible/inventory/prod/topology.json "$source/topology.json"
  scp $options -r "$source" "$remote:$temporary/stack"
  source_remote="$temporary/stack"
else
  source_remote=/opt/homelab/compose
  GITHUB_SHA=$(ssh $options "$remote" 'cat /opt/homelab/compose/.revision')
fi
ssh $options "$remote" "chmod 600 '$temporary/apps.json'; python3 '$source_remote/deploy.py' --source '$source_remote' --secret-bundle '$temporary/apps.json' --revision '$GITHUB_SHA'"
