#!/usr/bin/env bash
# set -e

# counter=1
for counter in {1..30}
do 
    echo "******* Iteration $counter"
    # python configure_pacman.py
    python network_larpix.py
    source config_util/sample_data_toggler_thresholds.sh asic_configs/st_trim/ 10 1 10
    # python power_down_larpix.py
done