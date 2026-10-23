#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "usage: $0 N[,M...]" >&2; exit 2; }
source "$(dirname "$0")/common.sh"
select_iogs "$1"
[[ -f .envrc ]] || exit 0
source .envrc
for iog in "${iogs[@]}"; do
  var="IOG${iog}_PID"
  pid="${!var:-0}"
  if [[ "$pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$pid" 2>/dev/null; then kill "$pid"; fi
done
