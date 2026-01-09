

# TO DO: FIX ISSUE WITH MULTIPLE IO CHANNEL NETWORKS
# RIGHT NOW SECOND IO_CHANNEL CONTROLLER OVERWRITES FIRST


import larpix
import larpix.io
from base import pacman_base
from base import network_base_FSD_v3
from base import utility_base
from base import generate_config
import argparse
import json
import time
from time import perf_counter
import shutil
from base import config_loader
from tqdm import tqdm
from analysis import plot_hydra_network_10x16

from runenv import runenv as RUN

import sys
import os

module = sys.modules[__name__]
for var in RUN.config.keys():
    setattr(module, var, getattr(RUN, var))

_default_file_prefix = None
_default_disable_logger = True
_default_verbose = True
_default_v_cm_lvds_tx = 5
_default_tx_diff = 7
_default_tx_slice = 15
_default_r_term = 7
_default_i_rx = 7
_default_recheck = False

v2d_10x16_root_ids = [21, 61, 101, 151]
v3_10x16_root_ids = [151, 111, 61, 21]

def main(io_group, file_prefix=_default_file_prefix,
         disable_logger=_default_disable_logger,
         verbose=_default_verbose,
         v_cm_lvds_tx=_default_v_cm_lvds_tx,
         tx_diff=_default_tx_diff,
         tx_slice=_default_tx_slice,
         r_term=_default_r_term,
         i_rx=_default_i_rx,
         pacman_tile=None,
         **kwargs):

    start = time.time()

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True, asic_version=3)
    c.io.reset_tiles([pacman_tile], length=4096*4, io_group=io_group)  # 2048
    time.sleep(4096*4*1e-6)
    c.io.reset_tiles([pacman_tile], length=4096*4, io_group=io_group)  # 2048
    time.sleep(4096*4*1e-6)

    for iog in [io_group]:
        print('Working on io_group={}'.format(iog))
        if io_group_asic_version_[iog] in [3]:

            tiles=pacman_tile
            print('Working on tiles: ', pacman_tile)

            if pacman_tile is None:
                tiles = io_group_pacman_tile_[iog]
            else:
                tiles = [pacman_tile]
            for tile in tiles:

                root_keys = []
                io_channels = utility_base.tile_to_io_channel([tile])
                for io_channel in io_channels:
                    cid =  v3_10x16_root_ids[ (io_channel-1) % 4]
                    network_base_FSD_v3.network_ext_node_from_tuple(c, iog, io_channel, cid)
                    candidate_root = network_base_FSD_v3.setup_root(c, c.io, iog, \
                                                          io_channel,\
                                                          cid, verbose, \
                                                          io_group_asic_version_[iog], 
                                                          v_cm_lvds_tx, tx_diff, tx_slice, r_term, i_rx,)
                    if candidate_root!=None: root_keys.append(candidate_root)

                print('ROOT KEYS: ', root_keys)
                
                iog_tile_to_root_keys = utility_base.partition_chip_keys_by_io_group_tile(
                    root_keys)

                for iog_tile in iog_tile_to_root_keys.keys():
                    network_base_FSD_v3.test_all_links(c, c.io, iog_tile[0],
                                                     iog_tile_to_root_keys[iog_tile],
                                                     verbose,
                                                     io_group_asic_version_[
                                                         iog], v_cm_lvds_tx,
                                                     tx_diff, tx_slice, r_term, i_rx, 
                                                     exclude=iog_exclude[iog], exclude_links=iog_exclude_links[iog])

            end = time.time()

            print('Time elapsed: ', end-start, ' s.')
            return c, c.io


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--io_group', default=None,
                        type=int, help='''io group to network''')
    parser.add_argument('--pacman_tile', default=None, \
                        type=int, help='''PACMAN tile to work with''') 
    parser.add_argument('--file_prefix', default=_default_file_prefix,
                        type=str, help='''String prepended to filename''')
    parser.add_argument('--disable_logger', default=_default_disable_logger,
                        action='store_true', help='''Disable logger''')
    parser.add_argument('--verbose', default=_default_verbose,
                        action='store_true', help='''Enable verbose mode''')
    parser.add_argument('--v_cm_lvds_tx',
                        default=_default_v_cm_lvds_tx,
                        type=int,
                        help='''Trim DAC for primary reference current''')
    parser.add_argument('--tx_diff',
                        default=_default_tx_diff,
                        type=int,
                        help='''Differential per-slice loop current DAC''')
    parser.add_argument('--tx_slice',
                        default=_default_tx_slice,
                        type=int,
                        help='''Slices enabled per transmitter DAC''')
    parser.add_argument('--r_term',
                        default=_default_r_term,
                        type=int,
                        help='''Receiver termination DAC''')
    parser.add_argument('--i_rx',
                        default=_default_i_rx,
                        type=int,
                        help='''Receiver bias current DAC''')
    args = parser.parse_args()
    main(**vars(args))
