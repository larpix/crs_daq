#!/usr/bin/env bash

if [ $# -eq 0 ]; then
	echo "NOTHING POWERED DOWN--Specify which modules!"
fi

if [[ "$1" == *"0"* ]]; then
python power_down_larpix.py --pacman_config io/pacman_m0.json 
echo "Module0 powered down"
fi

if [[ "$1" == *"1"* ]]; then
python power_down_larpix.py --pacman_config io/pacman_m1.json 
echo "Module1 powered down"
fi

if [[ "$1" == *"2"* ]]; then
python power_down_larpix.py --pacman_config io/pacman_m2.json 
echo "Module2 powered down"
fi

if [[ "$1" == *"3"* ]]; then
python power_down_larpix.py --pacman_config io/pacman_m3.json 
echo "Module3 powered down"
fi
