import larpix
import larpix.io
from base import pacman_base
from base import network_base_FSD_v3
from base import enforce_parallel
from base import utility_base
from base import generate_config
import argparse
import json
import time
from time import perf_counter
import shutil
from base import config_loader
from tqdm import tqdm

from runenv import runenv as RUN

import sys
import os

verbose = False

def main(io_group,pacman_config,asic_config,
         **kwargs):

    # create a larpix controller
    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True, asic_version=3, config_filepath=pacman_config)

    # Loads the chip configurations from the current asic configure directory
    config_loader.load_config_from_directory(c, asic_config)

    io_channels = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40]
    
    # Loops through each of the io channels and 
    for io_channel in io_channels:

        try:
            # Fetch a list of the chip ids from the controller
            keys = c.get_network_keys(io_group, io_channel)
            if verbose: print(f"Network Keys: {keys}")    
            print(f'\nStarting external trigger on io_channel {io_channel}')
        
        except:
            # Skips any io_channels that are not configured properly
            print(f'\nSkipping io channel {io_channel}')
            continue
        
        for key in keys:
            # Send the read request
            c[key].config.enable_external_sync = 0
            c.write_configuration(key, 'enable_external_sync')            
            c[key].config.enable_external_trigger = 1
            c.write_configuration(key, 'enable_external_trigger')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--io_group', default=1,
                        type=int, help='''io group to network''')
    #parser.add_argument('--pacman_tile', default=1, \
    #                    type=int, help='''PACMAN tile to work with''')
    parser.add_argument('--pacman_config',
                        default='io/pacman.json',
                        type=str,
                        help='''PACMAN config file to use''')
    parser.add_argument('--asic_config', default=None, \
                        type=str, help='''ASIC config to load and enforce''')
    #parser.add_argument('--file_prefix', default=_default_file_prefix,
    #                    type=str, help='''String prepended to filename''')
    #parser.add_argument('--disable_logger', default=_default_disable_logger,
    #                    action='store_true', help='''Disable logger''')
    #parser.add_argument('--verbose', default=False,
    #                    action='store_true', help='''Enable verbose mode''')
    args = parser.parse_args()
    main(**vars(args))
