#!/usr/bin/env bash
# Only the three applications without a usable online capture boundary stop.
set -Eeuo pipefail
umask 077
kube() { kubectl --request-timeout=10s "$@"; }
read_state() { kube -n homelab get configmap backup-capture-state -o json; }
write_state() {
  jq --argjson state "$2" '.data.state=($state|tojson)' <<<"$1" |
    kube replace -f - -o json >/dev/null
}
request_resume() {
  local object state
  object=$(read_state) || return 1
  state=$(jq -r '.data.state' <<<"$object")
  if jq -e --arg run "$run" '.run==$run and .phase=="capture"' <<<"$state" >/dev/null; then
    write_state "$object" "$(jq --argjson valid "$1" --argjson now "$(date +%s)" '
      .valid=($valid and $now < .deadline) | .phase="resume"' <<<"$state")"
  fi
}
recover() {
  local object state phase now failed name count suspended deadline remaining
  object=$(read_state) || return 1
  state=$(jq -er '.data.state' <<<"$object") || return 1
  phase=$(jq -r '.phase' <<<"$state"); now=$(date +%s)
  if [[ $phase == done && $now -ge $(jq -r '.deadline' <<<"$state") ]]; then
    write_state "$object" '{"phase":"idle"}' || return 1
    return
  fi
  if [[ $phase != resume && $phase != recovering ]]; then
    [[ $phase == capture && $now -ge $(jq -r '.deadline' <<<"$state") ]] || return 0
    state=$(jq '.valid=false' <<<"$state")
  fi
  state=$(jq '.phase="recovering"' <<<"$state")
  write_state "$object" "$state" || return 1
  deadline=$(jq -r '.deadline' <<<"$state")
  failed=false
  while IFS=$'\t' read -r name count; do
    kube -n homelab scale deployment "$name" --replicas="$count" >/dev/null || failed=true
  done < <(jq -r '.replicas[] | @tsv' <<<"$state")
  suspended=$(jq -r '.suspended' <<<"$state")
  kube -n flux-system patch kustomization apps --type=merge -p "{\"spec\":{\"suspend\":$suspended}}" >/dev/null || failed=true
  if [[ $failed == false ]]; then
    while IFS=$'\t' read -r name count; do
      if [[ $count -gt 0 ]]; then
        remaining=$((deadline + 180 - $(date +%s)))
        [[ $remaining -gt 0 ]] || remaining=1
        [[ $remaining -le 120 ]] || remaining=120
        kube -n homelab rollout status deployment/"$name" --timeout="$remaining"s >/dev/null || failed=true
      fi
    done < <(jq -r '.replicas[] | @tsv' <<<"$state")
  fi
  [[ $failed == false ]] || return 1
  object=$(read_state) || return 1
  [[ $(jq -r '.data.state|fromjson|.run' <<<"$object") == $(jq -r '.run' <<<"$state") ]] || return 1
  state=$(jq --argjson now "$(date +%s)" --argjson limit "$((deadline + 180))" '
    .valid = (.valid and $now <= $limit) | .phase="done" | .deadline=($now + 60)' <<<"$state")
  write_state "$object" "$state"
}
watchdog() {
  while true; do
    recover >/dev/null 2>&1 || echo 'Capture recovery/API error; retrying' >&2
    sleep 2
  done
}
wait_stopped() {
  local pods count
  while true; do
    pods=$(kube -n homelab get pods -l app="$1" -o json) || return 1
    count=$(jq -er '.items | length' <<<"$pods") || return 1
    [[ $count -gt 0 ]] || return 0
    [[ $(date +%s) -lt $(($2 - 30)) ]] || return 1
    sleep 2
  done
}
copy_keys() {
  local key
  mkdir -p "$2"
  for key in "$1"/*; do
    [[ -e $key ]] || continue
    cp -RL "$key" "$2/"
  done
}
capture() {
  local app=$1 object state owner deadline remaining phase snapshot result original
  local targets sources excludes=''
  # Fixed source names below deliberately use shell word splitting.
  case $app in
    opencloud) targets='opencloud qbittorrent'; sources='/source/opencloud-config /source/opencloud-data' ;;
    zipline) targets=zipline; sources='/source/zipline-data /source/zipline-uploads'; excludes='--exclude /source/zipline-uploads/.tmp' ;;
    n8n) targets=n8n; sources=/source/n8n-data ;;
    *) return 1 ;;
  esac
  copy_keys /credentials /recovery
  sources="$sources /recovery"
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=us-west-004
  AWS_ACCESS_KEY_ID=$(cat /repository/access-key-id)
  AWS_SECRET_ACCESS_KEY=$(cat /repository/secret-access-key)
  export RESTIC_PASSWORD_FILE=/repository/restic-password RESTIC_CACHE_DIR=/tmp/restic-cache
  local remote=s3:https://s3.us-west-004.backblazeb2.com/homelab-backup-b2/k8up/homelab/$app
  local repository=/stage/$app
  mkdir -p "$repository"
  # This local cache has no cloud fallback. The cloud repository is initialized by K8up.
  if [[ ! -e $repository/config ]]; then
    restic -r "$repository" init >/dev/null 2>&1
  fi
  result=0
  restic -r "$repository" backup --host homelab --tag warmup $excludes $sources >/dev/null 2>&1 || result=$?
  [[ $result == 0 || $result == 3 ]] || return 1
  restic -r "$repository" forget --keep-last 2 --prune >/dev/null 2>&1
  [[ $(kube -n homelab get deployment backup-watchdog -o jsonpath='{.status.readyReplicas}') == 1 ]] || return 1
  run=$(cat /proc/sys/kernel/random/uuid)
  while true; do
    object=$(read_state)
    if [[ $(jq -r '.data.state|fromjson|.phase' <<<"$object") != idle ]]; then sleep 2; continue; fi
    original=$(kube -n flux-system get kustomization apps -o json)
    jq -e '[.status.conditions[]? | select(.type=="Reconciling" and .status=="True")]|length==0' <<<"$original" >/dev/null || return 1
    state=$(jq -n --arg run "$run" --argjson deadline "$(( $(date +%s) + 420 ))" \
      --argjson suspended "$(jq '.spec.suspend // false' <<<"$original")" \
      '{phase:"capture",run:$run,deadline:$deadline,suspended:$suspended,replicas:[],valid:false}')
    for owner in $targets; do
      original=$(kube -n homelab get deployment "$owner" -o json)
      state=$(jq --arg name "$owner" --argjson count "$(jq '.spec.replicas // 1' <<<"$original")" '.replicas += [[$name,$count]]' <<<"$state")
    done
    if write_state "$object" "$state" 2>/dev/null; then break; fi
  done
  trap 'request_resume false >/dev/null 2>&1 || true' EXIT
  deadline=$(jq -r '.deadline' <<<"$state")
  [[ $(date +%s) -lt $((deadline - 30)) ]] || return 1
  kube -n flux-system patch kustomization apps --type=merge -p '{"spec":{"suspend":true}}' >/dev/null
  for owner in $targets; do
    [[ $(date +%s) -lt $((deadline - 30)) ]] || return 1
    kube -n homelab scale deployment "$owner" --replicas=0 >/dev/null
  done
  deadline=$(jq -r '.deadline' <<<"$state")
  for owner in $targets; do wait_stopped "$owner" "$deadline"; done
  remaining=$(( deadline - $(date +%s) - 30 )); [[ $remaining -gt 0 ]] || return 1
  timeout -k 5 "$remaining" restic -r "$repository" backup --json --host homelab --tag "data,$app" $excludes $sources > /tmp/capture-result 2>/dev/null
  snapshot=$(jq -r 'select(.message_type=="summary")|.snapshot_id' /tmp/capture-result)
  [[ $snapshot =~ ^[0-9a-f]{64}$ ]] || return 1
  request_resume true
  while true; do
    object=$(read_state) || return 1
    state=$(jq -er '.data.state' <<<"$object") || return 1
    [[ $(jq -r '.run' <<<"$state") == "$run" ]] || return 1
    phase=$(jq -r '.phase' <<<"$state")
    if [[ $phase == done ]]; then
      write_state "$object" '{"phase":"idle"}'
      [[ $(jq -r '.valid' <<<"$state") == true ]] || return 1
      break
    fi
    [[ $(date +%s) -le $((deadline + 180)) ]] || return 1
    sleep 2
  done
  trap - EXIT
  restic -r "$remote" copy --from-repo "$repository" --from-password-file "$RESTIC_PASSWORD_FILE" "$snapshot" >/dev/null 2>&1
  restic -r "$repository" forget --keep-last 2 --prune >/dev/null 2>&1
  printf '{"app":"%s","source_snapshot":"%s"}\n' "$app" "$snapshot"
}
if [[ $0 == *offline.sh ]]; then
  if [[ $# -ge 1 && $1 == watchdog ]]; then
    watchdog
  else
    # A subshell keeps errexit active inside capture while sanitizing native errors.
    (capture "$1") 2>/dev/null
  fi
fi
