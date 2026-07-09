#!/usr/bin/env bash

# Usage:
#   ./disable_hot_pixel_list.sh hot_pixels.txt /path/to/asic_configs
#
# Example:
#   ./disable_hot_pixel_list.sh hot_pixels.txt /data/CRS/asic_configs/ParameterScan/NominalConfigs

set -e

HOTPIXELS_FILE="$1"
ASIC_CONFIG="$2"
SCRIPT_PATH="config_util/disable_single_channel.py"

if [[ -z "$HOTPIXELS_FILE" ]]; then
    echo "No input file specified."
    echo "Usage: $0 hot_pixels.txt /path/to/asic_configs"
    exit 1
fi

if [[ -z "$ASIC_CONFIG" ]]; then
    echo "No ASIC config folder specified."
    echo "Usage: $0 hot_pixels.txt /path/to/asic_configs"
    exit 1
fi

if [[ ! -f "$HOTPIXELS_FILE" ]]; then
    echo "File not found: $HOTPIXELS_FILE"
    exit 1
fi

if [[ ! -d "$ASIC_CONFIG" ]]; then
    echo "ASIC config folder not found: $ASIC_CONFIG"
    exit 1
fi

echo "Disabling hot pixels listed in: $HOTPIXELS_FILE"
echo "Using ASIC config folder:       $ASIC_CONFIG"
echo "------------------------------------------------"

while read -r tag count fraction rest; do
    # skip empty lines or comment lines
    [[ -z "$tag" || "$tag" =~ ^# ]] && continue

    echo "Disabling: $tag"

    python "$SCRIPT_PATH" \
        --asic_config "$ASIC_CONFIG" \
        --channel "$tag"

done < "$HOTPIXELS_FILE"

echo "Done."
