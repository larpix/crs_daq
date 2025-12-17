#!/usr/bin/env bash

cfgdir=tmp/
rm -rf $cfgdir
python3 archive.py --monitor_dir $cfgdir

