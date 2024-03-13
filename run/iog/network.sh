#!/usr/bin/env bash

now=`date +%Y_%m_%d_%H_%M_%Z`
config_dir="asic_configs/asic_configs-$now"
mkdir $config_dir

config_dir_m0="$config_dir/m0"
config_dir_m1="$config_dir/m1"
config_dir_m2="$config_dir/m2"
config_dir_m3="$config_dir/m3"

## Module 0
########################################
if [[ "$1" == *"1"* ]]; then
	mkdir $config_dir_m0
	python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io1.json --config_path $config_dir_m0 &
	echo "Saving Module0 TPC1 configuration files to: $config_dir_m0"
fi

if [[ "$1" == *"2"* ]]; then
	if ! [ -d $config_dir_m0 ]; then
  		mkdir $config_dir_m0
	fi
	python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io2.json --config_path $config_dir_m0 &
	echo "Saving Module0 TPC2 configuration files to: $config_dir_m0"
fi

## Module 1
#######################################
if [[ "$1" == *"3"* ]]; then
        mkdir $config_dir_m1
        python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io1.json --config_path $config_dir_m1 &
        echo "Saving Module1 TPC1 configuration files to: $config_dir_m1"
fi

if [[ "$1" == *"4"* ]]; then
        if ! [ -d $config_dir_m1 ]; then
                mkdir $config_dir_m1
        fi
        python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io4.json --config_path $config_dir_m1 &
        echo "Saving Module1 TPC2 configuration files to: $config_dir_m1"
fi

#######################################

## Module 2
#######################################
if [[ "$1" == *"5"* ]]; then
        mkdir $config_dir_m2
        python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io5.json --config_path $config_dir_m2 &
        echo "Saving Module2 TPC2 configuration files to: $config_dir_m2"
fi

if [[ "$1" == *"6"* ]]; then
        if ! [ -d $config_dir_m2 ]; then
                mkdir $config_dir_m2
        fi
        python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io6.json --config_path $config_dir_m2 &
        echo "Saving Module2 TPC2 configuration files to: $config_dir_m2"
fi

#######################################

## Module 3
#######################################
if [[ "$1" == *"7"* ]]; then
        mkdir $config_dir_m3
        python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io7.json --config_path $config_dir_m3 &
        echo "Saving Module3 TPC1 configuration files to: $config_dir_m3"
fi

if [[ "$1" == *"8"* ]]; then
        if ! [ -d $config_dir_m3 ]; then
                mkdir $config_dir_m3
        fi
        python network_larpix.py --controller_config configs/controller_config.json --pacman_config io/pacman_io8.json --config_path $config_dir_m3 &
        echo "Saving Module3 TPC2 configuration files to: $config_dir_m3"
fi

#######################################
