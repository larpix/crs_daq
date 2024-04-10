#!/usr/bin/env bash

now=`date +%Y_%m_%d_%H_%M_%S_%Z`
config_dir="asic_configs/asic_configs-$now"
mkdir $config_dir

config_dir_m0="$config_dir/m0"
config_dir_m1="$config_dir/m1"
config_dir_m2="$config_dir/m2"
config_dir_m3="$config_dir/m3"

if [[ "$1" == *"0"* ]]; then
	mkdir $config_dir_m0
	python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_m0.json --config_path $config_dir_m0 --pid_logged &
	PID=$!
	sed -i "s/MOD0_PID=[0-9]*/MOD0_PID=${PID}/" .envrc
	echo "Saving Module0 configuration files to: $config_dir_m0"
fi

if [[ "$1" == *"1"* ]]; then
	mkdir $config_dir_m1
	python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_m1.json --config_path $config_dir_m1 --pid_logged &
	PID=$!
	sed -i "s/MOD1_PID=[0-9]*/MOD1_PID=${PID}/" .envrc
	echo "Saving Module1 configuration files to: $config_dir_m1"
fi

if [[ "$1" == *"2"* ]]; then
	mkdir $config_dir_m2
	python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_m2.json --config_path $config_dir_m2 --pid_logged  &
	PID=$!
	sed -i "s/MOD2_PID=[0-9]*/MOD2_PID=${PID}/" .envrc
	echo "Saving Module2 configuration files to: $config_dir_m2"
fi

if [[ "$1" == *"3"* ]]; then
	mkdir $config_dir_m3
	python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_m3.json --config_path $config_dir_m3 --pid_logged  &
	PID=$!
	sed -i "s/MOD3_PID=[0-9]*/MOD3_PID=${PID}/" .envrc
	echo "Saving Module3 configuration files to: $config_dir_m3"
fi
