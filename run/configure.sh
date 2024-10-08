#!/usr/bin/env bash

	
if [[ $# -eq 2 ]]; then
	CONFIG_DIR=$2
	echo "Using asic_config dir $CONFIG_DIR"

	if [[ "$1" == *"1"* ]]; then
	
		echo "launching into screen 'IOG1_configure'"

		if ! screen -list | grep -q "IOG1_configure"; then
			screen -S IOG1_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog1.json --config_subdir i1 --asic_config $CONFIG_DIR --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi
	if [[ "$1" == *"2"* ]]; then

		echo "launching into screen 'IOG2_configure'"

		if ! screen -list | grep -q "IOG2_configure"; then
			screen -S IOG2_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog2.json --config_subdir i2 --asic_config $CONFIG_DIR --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi
	if [[ "$1" == *"3"* ]]; then
	
		echo "launching into screen 'IOG3_configure'"

		if ! screen -list | grep -q "IOG3_configure"; then
			screen -S IOG3_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog3.json --config_subdir i3 --asic_config $CONFIG_DIR --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi	
	if [[ "$1" == *"4"* ]]; then

		echo "launching into screen 'IOG4_configure'"

		if ! screen -list | grep -q "IOG4_configure"; then
			screen -S IOG4_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog4.json --config_subdir i4 --asic_config $CONFIG_DIR --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi
else

	
	if [[ "$1" == *"1"* ]]; then
	
		echo "launching into screen 'IOG1_configure'"

		if ! screen -list | grep -q "IOG1_configure"; then
			screen -S IOG1_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog1.json --config_subdir i1  --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi
	if [[ "$1" == *"2"* ]]; then

		echo "launching into screen 'IOG2_configure'"

		if ! screen -list | grep -q "IOG2_configure"; then
			screen -S IOG2_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog2.json --config_subdir i2 --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi
	if [[ "$1" == *"3"* ]]; then
	
		echo "launching into screen 'IOG3_configure'"

		if ! screen -list | grep -q "IOG3_configure"; then
			screen -S IOG3_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog3.json --config_subdir i3 --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi
	fi		
	if [[ "$1" == *"4"* ]]; then

		echo "launching into screen 'IOG4_configure'"

		if ! screen -list | grep -q "IOG4_configure"; then
			screen -S IOG4_configure -dm bash -c "python configure_larpix.py --pacman_config io/pacman_iog4.json --config_subdir i4 --pid_logged; sleep 10"
		else
			echo "warning: nothing happened, screen already running with same name. Kill this screen to continue"
		fi

	fi
fi




