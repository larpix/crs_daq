#!/usr/bin/env bash
# set -e

now=`date +%Y_%m_%d_%H_%M_%S_%Z`

current_config=$1

python configure_larpix.py --asic_config $current_config
# python configure_larpix.py --asic_config $current_config


filename="toggle-data-sample-binary-$now.h5"
full_packet="./toggle-data-sample-packet-$now.h5"
echo "Writing sample data file to: $full_path"

python record_data.py --filename $filename --runtime $2 --file_count 1
echo "converting to $full_packet"
python ../../test/larpix-control/scripts/convert_rawhdf5_to_hdf5.py -i $filename -o $full_packet
python analysis/plot_metric.py --metric rate --filename $full_packet

toggle_filename="toggle-list-$now.json" 
python config_util/toggle_trims_from_rate.py --filename $full_packet --min_rate $3 --max_rate $4 --toggle_filename $toggle_filename

python config_util/merge_toggle_list_to_config.py $current_config/* --toggle_json $toggle_filename

python analysis/plot_pixel_trim_dac.py --asic_config $current_config
echo "#                                                                 #"
echo "#                                                                 #"
echo "#                                                                 #"
echo "#   New ASIC configs written to: $current_config"
echo "#                                                                 #"
echo "#                                                                 #"
echo "#                                                                 #"