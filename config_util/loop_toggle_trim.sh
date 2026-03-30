#!/usr/bin/env bash
# set -e

# counter=1
for counter in {1..10}
do 
    echo "******* Iteration $counter"
    # python configure_pacman.py
    python network_larpix.py
    source config_util/sample_data_toggler_thresholds.sh asic_configs/test_thresh_t10_not_quite_cold/ 5 0.1 1 data/
    # python power_down_larpix.py
done
