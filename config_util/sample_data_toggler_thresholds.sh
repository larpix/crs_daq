#!/usr/bin/env bash
# set -e

###### NOTE: SET $5 TO MATCH 'destination_dir_' IN RUN_CONFIG!

now=`date +%Y_%m_%d_%H_%M_%S_%Z`

current_config=$1

python configure_larpix.py --asic_config $current_config

filename="toggle-data-sample-packet-$now.h5"
echo "Writing sample data file to: $5/$filename"

python record_data.py --packet --filename $filename --runtime $2 --file_count 1

toggle_filename="toggle-list-$now.json" 
python config_util/toggle_trims_from_rate.py --filename $5/$filename --min_rate $3 --max_rate $4 --toggle_filename $toggle_filename

python config_util/merge_toggle_list_to_config.py $current_config/* --toggle_json $toggle_filename

python analysis/plot_pixel_trim_dac.py --asic_config $current_config

python analysis/plot_metric_pedestal.py --filename $5/$filename --metric rate

echo "#                                                                 #"
echo "#                                                                 #"
echo "#                                                                 #"
echo "#   New ASIC configs written to: $current_config"
echo "#                                                                 #"
echo "#                                                                 #"
echo "#                                                                 #"
