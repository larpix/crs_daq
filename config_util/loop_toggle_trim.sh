#!/usr/bin/env bash
# set -e

# counter=1
for counter in {1..20}
do 
    echo "******* Iteration $counter"
    # python configure_pacman.py
    python network_larpix.py
    source config_util/sample_data_toggler_thresholds.sh asic_configs/toggle_trim_0724_try3/ 40 0.01 0.1
    # python power_down_larpix.py
done
