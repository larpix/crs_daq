#!/usr/bin/env bash

#COMMAND LINE ARGUMENTS
# $1 FILENAME
# $2 RUN NUMBER
# $3 DATA-STREAM

cfgdir=/tmp/MORCS_CONFIGS
python config_util/embed_config.py --use-destination-dir $1 $cfgdir/*
python dump_metadata.py $1 --run $2 --data-stream $3
