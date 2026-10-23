#!/usr/bin/env bash
set -euo pipefail

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: $0 N[,M...] [asic-config-directory]" >&2; exit 2; }
config_dir="${2:-}"
source "$(dirname "$0")/common.sh"
select_iogs "$1"

mkdir -p log
stamp=$(date +%Y_%m_%d_%H_%M_%S_%Z)
pids=()
for iog in "${iogs[@]}"; do
  args=(--pacman_config "io/pacman_io${iog}.json" --pid_logged)
  if [[ -n "$config_dir" ]]; then
    args+=(--asic_config "$config_dir" --config_subdir "iog${iog}")
  fi
  python configure_larpix.py "${args[@]}" \
    >"log/configure-iog${iog}-${stamp}.log" 2>&1 &
  pid=$!
  pids+=("$pid")
  log_pid "$iog" "$pid"
  echo "IOG ${iog}: PID ${pid}, log/configure-iog${iog}-${stamp}.log"
done

status=0
for pid in "${pids[@]}"; do wait "$pid" || status=$?; done
exit "$status"
