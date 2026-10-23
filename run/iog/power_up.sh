#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "usage: $0 N[,M...]" >&2; exit 2; }
source "$(dirname "$0")/common.sh"
select_iogs "$1"
for iog in "${iogs[@]}"; do
  python configure_pacman.py --pacman_config "io/pacman_io${iog}.json" --verbose
done
