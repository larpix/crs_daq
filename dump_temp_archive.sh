#!/usr/bin/env bash

cfgdir=/tmp/MORCS_CONFIGS
rm -rf $cfgdir
python3 archive.py --monitor_dir $cfgdir

