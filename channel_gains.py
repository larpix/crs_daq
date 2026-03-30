import larpix
import larpix.io
import argparse
import time
import json
import copy
import numpy as np
from runenv import runenv as RUN
from base import pacman_base
from base import config_loader
from base import enforce_parallel
from base import utility_base
from base.utility_base import *
import os

import sys
from runenv import runenv as RUN
module = sys.modules[__name__]
for var in RUN.config.keys():
    setattr(module, var, getattr(RUN, var))

#Write configuration to multiple io_channels/tiles via broadcast chip ID 255
def multitile_write(c, register, value, io_channels, keys, disabled_list, channel, verbose=False):

    for io_channel in io_channels:
        
        for key in keys[io_channel]:
            
            if key in list(disabled_list.keys()) and channel in disabled_list[key]:
                continue

            setattr(c[key].config, register, value)
            c.write_configuration(key, register)

    return
            
def get_keys(c, io_group, tiles):

    keys = [[] for ioch in range(41)]   
    
    io_channels = utility_base.tile_to_io_channel(tiles)
    print(f"\nTesting io_channels {io_channels}")
    
    for io_channel in io_channels:

        try:
            # Fetch a list of the chip keys from the controller
            keys[io_channel] = c.get_network_keys(io_group, io_channel)
            print(f"\nIO_channel {io_channel} Keys: \n{keys[io_channel]}")
            
        except:
            # Skips any channels that are not configured properly
            print(f'\nSkipping io channel {io_channel}\n')
            keys[io_channel] = []
            continue
    
    return io_channels, keys

def get_disabled(disabled):
    
    disabled_list={}
    with open(disabled, 'r') as f: 
        disabled_list=json.load(f)
    
    # Get top-level keys
    print(f"disabled list:\n{list(disabled_list.keys())})")

    return disabled_list

def main(io_group, pacman_config, high_dac, disabled, **kwargs):

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(
        relaxed=True, config_filepath=pacman_config, asic_version=3)

    CONFIG = None
    with open(asic_config_paths_file_, 'r') as ff:
        d = json.load(ff)
        CONFIG = d[str(io_group)]
    config_loader.load_config_from_directory(c, CONFIG)

    pacman_base.enable_pacman_uart_from_io_channel(
            c.io, io_group, list(set([chip.io_channel for chip in c.chips])))
    
    tiles = io_group_pacman_tile_[io_group]

    io_channels, keys = get_keys(c,io_group, tiles)

    disabled_list = get_disabled(disabled)
    
    low_dac = 0
    n_iterations = 20

    for channel in range(64):
        print(f'Testing channel {channel}')

        #Unmask only the channel under test
        mask_list = [1]*64
        mask_list[channel] = 0
        multitile_write(c, 'channel_mask', mask_list, io_channels, keys, disabled_list, channel)

        print(f'Testing with high DAC {high_dac}')
        for i in range(n_iterations):

            #Set test pulse DAC low 
            multitile_write(c, 'csa_testpulse_dac', low_dac, io_channels, keys, disabled_list, channel)
            #time.sleep(0.05)
            
            #Set test pulse DAC high
            multitile_write(c, 'csa_testpulse_dac', high_dac, io_channels, keys, disabled_list, channel)
            #time.sleep(0.05)
                        
            #Fire test pulse
            #NOTE: csa_testpulse_enable IS ACTIVE LOW (0)
            multitile_write(c, 'csa_testpulse_enable', mask_list, io_channels, keys, disabled_list, channel)
            #time.sleep(0.05)
                
            #Disable test pulse
            #NOTE: csa_testpulse_enable IS INACTIVE HIGH (1)
            multitile_write(c, 'csa_testpulse_enable', [1]*64, io_channels, keys, disabled_list, channel)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--io_group',
                        default=1,
                        type=int,
                        help='''IO group''')
    parser.add_argument('--pacman_config',
                        default='io/pacman.json',
                        type=str,
                        help='''PACMAN config file''')
    parser.add_argument('--high_dac',
                        default=128,
                        type=int,
                        help='''High DAC level to test''')    
    parser.add_argument('--disabled', default=None, \
                        type=str, help='''file with disabled channels by chip key''')

    args = parser.parse_args()
    c = main(**vars(args))
    