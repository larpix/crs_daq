#!/usr/bin/env bash

# Validate every selected IOG before any control process starts. Read JSON only;
# importing runenv here would create or update deployment runtime files.
select_iogs() {
  local selection="${1:-}" iog
  if [[ ! "$selection" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]]; then
    echo "IO groups must be explicit positive integers: N or N,M (no 'all')" >&2
    return 2
  fi
  local -a requested
  IFS=, read -r -a requested <<< "$selection"
  iogs=()
  for iog in "${requested[@]}"; do
    [[ " ${iogs[*]} " == *" ${iog} "* ]] && continue
    if [[ ! -f "io/pacman_io${iog}.json" ]]; then
      echo "Missing PACMAN configuration: io/pacman_io${iog}.json" >&2
      return 2
    fi
    iogs+=("$iog")
  done
  python - "${iogs[@]}" <<'PY'
import json
import sys

try:
    with open('RUN_CONFIG.json') as infile:
        run = json.load(infile)
    for group in sys.argv[1:]:
        with open(f'io/pacman_io{group}.json') as infile:
            pairs = json.load(infile)['io_group']
        if len(pairs) != 1 or pairs[0][0] != int(group) or not pairs[0][1]:
            raise ValueError(f'IOG {group}: expected one matching PACMAN endpoint')
        if run['io_group_asic_version_'][group] not in ('2d', 3, '3'):
            raise ValueError(f'IOG {group}: unsupported ASIC family')
        if run['iog_pacman_version_'][group] != 'v1rev5':
            raise ValueError(f'IOG {group}: expected a Rev5 PACMAN')
        tiles = run['io_group_pacman_tile_'][group]
        if not tiles or any(type(tile) is not int or not 1 <= tile <= 10 for tile in tiles):
            raise ValueError(f'IOG {group}: expected connected tiles in 1–10')
        for tile in tiles:
            run['iog_VDDA_DAC'][group][tile - 1]
            run['iog_VDDD_DAC'][group][tile - 1]
            run['iog_exclude'][group][str(tile)]
            run['iog_exclude_links'][group][str(tile)]
except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
    sys.exit(f'Invalid deployment configuration: {error}')
PY
}

log_pid() {
  local iog="$1" pid="$2"
  touch .envrc
  if grep -q "^IOG${iog}_PID=" .envrc; then
    sed -i "s/^IOG${iog}_PID=.*/IOG${iog}_PID=${pid}/" .envrc
  else
    echo "IOG${iog}_PID=${pid}" >> .envrc
  fi
}
