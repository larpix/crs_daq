#!/usr/bin/env bash

if ! [ -f .default_asic_configs_.json ]; then
	echo "{}" > .default_asic_configs_.json
fi

default_file=$(jq '.default_asic_config_paths_file_' <<< cat RUN_CONFIG.json)
has_default_iog1=$(jq -r 'has("1")' <<< cat .default_asic_configs_.json)
has_default_iog2=$(jq -r 'has("2")' <<< cat .default_asic_configs_.json)
has_default_iog3=$(jq -r 'has("3")' <<< cat .default_asic_configs_.json)
has_default_iog4=$(jq -r 'has("4")' <<< cat .default_asic_configs_.json)

write_default_iog1=false
write_default_iog2=false
write_default_iog3=false
write_default_iog4=false
write_any=false

if ! $has_default_iog1; then
	write_default_iog1=true
	write_any=true
fi

if ! $has_default_iog2; then
	write_default_iog2=true
	write_any=true
fi

if ! $has_default_iog3; then
	write_default_iog3=true
	write_any=true
fi

if ! $has_default_iog4; then
	write_default_iog4=true
	write_any=true
fi


now=`date +%Y_%m_%d_%H_%M_%S_%Z`
config_dir="asic_configs/asic_configs-$now"
config_dir_iog1="$config_dir/i1"
config_dir_iog2="$config_dir/i2"
config_dir_iog3="$config_dir/i3"
config_dir_iog4="$config_dir/i4"

if $write_any; then	

	mkdir $config_dir

fi


if [[ "$1" == *"1"* ]]; then
	if $write_default_iog1; then
		mkdir $config_dir_iog1
		echo "Saving Module1 configuration files to: $config_dir_iog1"
	fi
	
	echo "config dir: $config_dir_iog1"
	echo "launching network into screen 'IOG1_network'"

	if ! screen -list | grep -q "IOG1_network"; then
		screen -S IOG1_network -dm bash -c "python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_iog1.json --config_path $config_dir_iog1 --pid_logged"
		gnome-terminal --title="IOG1_NETWORK" -- bash -c "screen -r IOG1_network"
	else
		echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
	#
	fi
	
fi

if [[ "$1" == *"2"* ]]; then
	if $write_default_iog2; then
		mkdir $config_dir_iog2
		echo "Saving IOG2 configuration files to: $config_dir_iog2"
	fi
	
	echo "config dir: $config_dir_iog2"
	echo "launching network into screen 'IOG2_network'"

	if ! screen -list | grep -q "IOG2_network"; then
		screen -S IOG2_network -dm bash -c "python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_iog2.json --config_path $config_dir_iog2 --pid_logged"
	gnome-terminal --title="IOG2_NETWORK" -- bash -c "screen -r IOG2_network"	
#PID=$!
	else
		echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
	#
	fi
	
fi

if [[ "$1" == *"3"* ]]; then
	if $write_default_iog3; then
		mkdir $config_dir_iog3
		echo "Saving Module1 configuration files to: $config_dir_iog3"
	fi
	
	echo "config dir: $config_dir_iog3"
	echo "launching network into screen 'module1_network'"

	if ! screen -list | grep -q "IOG3_network"; then
		screen -S IOG3_network -dm bash -c "python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_iog3.json --config_path $config_dir_iog3 --pid_logged"
		gnome-terminal --title="IOG3_NETWORK" -- bash -c "screen -r IOG3_network"
		#PID=$!

	else
		echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
	#
	fi
	
fi

if [[ "$1" == *"4"* ]]; then
	if $write_default_m4; then
		mkdir $config_dir_iog4
		echo "Saving IOG4 configuration files to: $config_dir_iog4"
	fi
	
	echo "config dir: $config_dir_iog4"
	echo "launching network into screen 'IOG4_network'"

	if ! screen -list | grep -q "IOG4_network"; then
		screen -S IOG4_network -dm bash -c "python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_iog4.json --config_path $config_dir_iog4 --pid_logged"
		gnome-terminal --title="IOG4_NETWORK" -- bash -c "screen -r IOG4_network"
	else
		echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
	#
	fi
	
fi

