#!/usr/bin/env bash
# set -e

# counter=1
for counter in {1..10}
do 
    echo "******* Iteration $counter"
    # python configure_pacman.py
    python network_larpix.py
    source config_util/sample_data_toggler_thresholds.sh asic_configs/asic_configs_2026_02_06_15_42_PST 5 0 1 ./data
    #source config_util/sample_data_toggler_thresholds.sh asic_configs/asic_configs_2026_02_07_16_03_PST 5 0.1 10 ./data
    # python power_down_larpix.py
done
