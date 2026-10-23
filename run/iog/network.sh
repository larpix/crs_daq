#!/usr/bin/env bash
set -euo pipefail

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: $0 N[,M...] [--update_default]" >&2; exit 2; }
extra_args=()
if [[ "${2:-}" == "--update_default" && $# -le 2 ]]; then
  extra_args+=(--update_default)
elif [[ $# -gt 1 ]]; then
  echo "usage: $0 N[,M...] [--update_default]" >&2
  exit 2
fi
source "$(dirname "$0")/common.sh"
select_iogs "$1"

mkdir -p log
stamp=$(date +%Y_%m_%d_%H_%M_%S_%Z)
config_dir="asic_configs/asic_configs-${stamp}"

pids=()
for iog in "${iogs[@]}"; do
  python network_larpix.py \
    --controller_config configs/controller_config.json \
    --pacman_config "io/pacman_io${iog}.json" \
    --config_path "$config_dir/iog${iog}" --pid_logged "${extra_args[@]}" \
    >"log/network-iog${iog}-${stamp}.log" 2>&1 &
  pid=$!
  pids+=("$pid")
  log_pid "$iog" "$pid"
  echo "IOG ${iog}: PID ${pid}, log/network-iog${iog}-${stamp}.log"
done

status=0
for pid in "${pids[@]}"; do wait "$pid" || status=$?; done
exit "$status"
