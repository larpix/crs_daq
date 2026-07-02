#!/usr/bin/env bash
set -euo pipefail

cfg_parent=""
generate_args=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --file_folder|--file-folder)
            cfg_parent="$2"
            shift 2
            ;;
        *)
            generate_args+=("$1")
            shift
            ;;
    esac
done

if [[ -z "$cfg_parent" ]]; then
    echo "Usage:"
    echo "  $0 --file_folder <parent folder> [generate_pedestal_config.py flags]"
    echo
    echo "Example:"
    echo "  $0 --file_folder asic_configs/run3-warmcommissioning_iog3_pacman34 \\"
    echo "     --periodic_trigger_cycles 578125 \\"
    echo "     --periodic_reset_cycles 4 \\"
    echo "     --vref_dac 185 \\"
    echo "     --vcm_dac 50"
    exit 1
fi

# Find repo root assuming this script lives in config_util/
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

# Allow either absolute paths or paths relative to the repo root
if [[ "$cfg_parent" = /* ]]; then
    cfg_parent_abs="$cfg_parent"
else
    cfg_parent_abs="$repo_root/$cfg_parent"
fi

shopt -s nullglob
config_files=( "$cfg_parent_abs"/m*/* )
shopt -u nullglob

if [[ ${#config_files[@]} -eq 0 ]]; then
    echo "ERROR: no config files found matching:"
    echo "  $cfg_parent_abs/m*/*"
    exit 1
fi

echo "Found ${#config_files[@]} config files."
echo
echo "Step 1/3: generating pedestal config..."
python "$repo_root/config_util/generate_pedestal_config.py" \
    "${config_files[@]}" \
    "${generate_args[@]}"

echo
echo "Step 2/3: enabling CSA disable channel mask..."
python "$repo_root/config_util/enable_csa_disable_channel_mask.py" \
    "${config_files[@]}"

echo
echo "Step 3/3: enabling rolling reset..."
python "$repo_root/config_util/enable_rolling_reset.py" \
    "${config_files[@]}"

echo
echo "Done."
