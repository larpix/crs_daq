#!/usr/bin/env bash

#COMMAND LINE ARGUMENTS
# $1 FILENAME

#cfgdir=/tmp/MORCS_CONFIGS
cfgdir=tmp/
python3 config_util/embed_config.py --use-destination-dir $1 $cfgdir/*
