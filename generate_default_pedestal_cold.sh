#!/usr/bin/env bash

# Generate warm pedestal, command line argument is files to use

cp -r $1 asic_configs/PEDESTAL_CONFIG
python config_util/generate_pedestal_config.py asic_configs/PEDESTAL_CONFIG/* --vref_dac 223 --vcm_dac 68
python config_util/enable_csa_disable_channel_mask.py asic_configs/PEDESTAL_CONFIG/*
