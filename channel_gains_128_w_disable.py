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
def multitile_write(c, io_group, tiles, register, value, io_channels, keys, disabled, channel, verbose=False):

    for io_channel in io_channels:
        for key in keys[io_channel]:                
            chip = key.chip_id
            if channel in disabled[io_channel][chip]:
                return

            setattr(c[key].config, register, value)
            c.write_configuration(key, register)
            #if io_channel == io_channels[0]:
            #    print(f"Writing register {register} to {value} on io_channel {io_channel} key {key}")

    return
            
def get_keys(c, io_group, tiles):

    keys = [[] for ioch in range(40)]   
    
    io_channels = utility_base.tile_to_io_channel(tiles)
    print(f"\nTesting io_channels {io_channels}")
    
    for io_channel in io_channels:

        try:
            # Fetch a list of the chip ids from the controller
            keys[io_channel] = c.get_network_keys(io_group, io_channel)
            print(f"\nIO_channel {io_channel} Keys: \n{keys[io_channel]}")    
        except:
            # Skips any channels that are not configured properly
            print(f'\nSkipping io channel {io_channel}\n')
            continue
    
    return io_channels, keys

def get_disabled(asic_config):

    disabled = [[[] for chip in range(171)] for ioch in range(41)]
    n_disabled = 0
    
    for file in asic_config:
        config = {}
        with open(file, 'r') as f:
            configs = json.load(f)
              
            for chan in range(64):
                if configs["channel_mask"][chan] == 1:
                    chip = configs[key]["chip_id"]
                    io_channel = configs[key]["io_channel"]
                    disabled[io_channel][chip].append(chan)
                    n_disabled +=1

    print(f"{n_disabled} disabled channels")

    return disabled

def main(pacman_config,
        io_group,
         asic_config,
         **kwargs):

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
    
    disabled = get_disabled(asic_config)
    
    low_dac = 0
    high_dacs = [128]
    n_iterations = 10

    for channel in range(64):
        print(f'Testing channel {channel}')

        #Unmask only the channel under test
        mask_list = [1]*64
        mask_list[channel] = 0
        multitile_write(c, io_group, tiles, 'channel_mask', mask_list, io_channels, keys, hot_channels, channel)

        for high_dac in high_dacs:
            print(f'Testing with high DAC {high_dac}')
            for i in range(n_iterations):
                #print(f'Iteration {i}')

                #Set test pulse DAC low 
                multitile_write(c, io_group, tiles, 'csa_testpulse_dac', low_dac, io_channels, keys, channel)
                #time.sleep(0.05)
            
                #Set test pulse DAC high
                multitile_write(c, io_group, tiles, 'csa_testpulse_dac', high_dac, io_channels, keys, channel)
                #time.sleep(0.05)
                        
                #Fire test pulse
                #NOTE: csa_testpulse_enable IS ACTIVE LOW (0)
                multitile_write(c, io_group, tiles, 'csa_testpulse_enable', mask_list, io_channels, keys, channel)
                #time.sleep(0.05)
                
                #Disable test pulse
                #NOTE: csa_testpulse_enable IS INACTIVE HIGH (1)
                multitile_write(c, io_group, tiles, 'csa_testpulse_enable', [1]*64, io_channels, keys, channel)


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
    parser.add_argument('--asic_config', 
                        type=str, 
                        default=None, 
                        help='''Asic Configs with disabled channels''')
    args = parser.parse_args()
    c = main(**vars(args))
    