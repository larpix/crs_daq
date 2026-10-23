

#TO DO: FIX ISSUE WITH MULTIPLE IO CHANNEL NETWORKS
#RIGHT NOW SECOND IO_CHANNEL CONTROLLER OVERWRITES FIRST


import larpix
import larpix.io
from base import pacman_base
from base import network_base_FSD as network_base
from base import utility_base
from base import generate_config
import argparse
import json
import time
from time import perf_counter
import shutil
from base import config_loader
from base.tile_layout import V2D_DONOR_ROOT_IDS, validate_physical_root_assignment
from tqdm import tqdm

import sys
import os
from runenv import runenv as RUN

module = sys.modules[__name__]
for var in RUN.config.keys():
    setattr(module, var, getattr(RUN, var))

_default_file_prefix=None
_default_disable_logger=True
_default_verbose=False
_default_ref_current_trim=0
_default_tx_diff=4
_default_tx_slice=7
_default_r_term=2
_default_i_rx=8
_default_recheck=False

def parse_root_map(values):
    mapping = {}
    for value in values or ():
        channel, chip_id = (int(part) for part in value.split(':', 1))
        mapping[channel] = chip_id
    return mapping

def main(io_group, file_prefix=_default_file_prefix, \
         disable_logger=_default_disable_logger, \
         verbose=_default_verbose, \
         ref_current_trim=_default_ref_current_trim, \
         tx_diff=_default_tx_diff, \
         tx_slice=_default_tx_slice, \
         r_term=_default_r_term, \
         i_rx=_default_i_rx,
         pacman_tile=None,\
         pacman_config=None,\
         physical_root_map=None,\
         **kwargs):
   
    if pacman_config is None:
        raise ValueError('--pacman_config is required')
    if io_group_asic_version_[io_group] != '2d':
        raise ValueError(f'IOG {io_group} is not configured as v2d')
    reset_tiles = [pacman_tile] if pacman_tile is not None else io_group_pacman_tile_[io_group]
    active_channels = utility_base.tile_to_io_channel(reset_tiles)
    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True, config_filepath=pacman_config, asic_version=2)
    pacman_base.configure_hijinks_uart_infrastructure(
        c.io, io_group, '2d', active_channels
    )
    c.io.reset_tiles(reset_tiles, length=4096*4, io_group=io_group)
    time.sleep(4096*4*1e-6)
    c.io.reset_tiles(reset_tiles, length=4096*4, io_group=io_group)
    time.sleep(4096*4*1e-6)

    _file_prefix = file_prefix

    if True:
        now=time.strftime("%Y_%m_%d_%H_%M_%Z")
        config_name='controller-config-'+now+'.json'
        #VERSION_SPECIFIC
   

    for iog in [io_group]:
        print('inverting io group {} tiles {}'.format(iog, io_group_pacman_tile_[iog]))
        pacman_base.invert_pacman_uart(c.io, iog, io_group_asic_version_[iog], \
                                       io_group_pacman_tile_[iog]) 
    
        print('Working on io_group={}'.format(iog))
        if io_group_asic_version_[iog]=='2d':
            tiles=pacman_tile
            if pacman_tile is None:
                tiles = io_group_pacman_tile_[iog]
            else:
                tiles = [pacman_tile]
            for tile in tiles:

                root_keys=[]
                unconfigured=[]
                io_channels = utility_base.tile_to_io_channel([tile])
                live_channels = [channel for channel in io_channels
                                 if channel not in pacman_base.DEAD_LOGICAL_CHANNELS]
                if physical_root_map:
                    root_ids = validate_physical_root_assignment(
                        parse_root_map(physical_root_map), live_channels)
                    print('using operator-confirmed physical root assignment:', root_ids)
                else:
                    # Repeat the donor connector order on each tile; retain
                    # its programmed IDs, including the third connector's 101.
                    root_ids = {channel: V2D_DONOR_ROOT_IDS[25 + (channel - 1) % 4]
                                for channel in live_channels}
                    print('using donor-programmed root IDs (not verified physical positions):', root_ids)
                for io_channel in io_channels:
                    if io_channel in pacman_base.DEAD_LOGICAL_CHANNELS:
                        print(f'skipping dead logical channel {io_channel}')
                        continue
                    cid = root_ids[io_channel]
                    network_base.network_ext_node_from_tuple(c, iog, io_channel, cid)
                    candidate_root = network_base.setup_root(c, c.io, iog, \
                                                          io_channel,\
                                                          cid, verbose, \
                                                          io_group_asic_version_[iog], \
                                                          0, 0, 15, 2, 8)
                    if candidate_root!=None: root_keys.append(candidate_root)
           
                print('ROOT KEYS: ',root_keys)

                if not root_keys:
                    raise RuntimeError(
                        f'No v2d roots replied on IOG {iog}, tile {tile}; '
                        'network JSON was not written'
                    )

                iog_tile_to_root_keys=utility_base.partition_chip_keys_by_io_group_tile(root_keys)
                print(iog_tile_to_root_keys)
                for iog_tile in iog_tile_to_root_keys.keys():
                    network_base.initial_network(c, c.io, iog_tile[0], \
                                             iog_tile_to_root_keys[iog_tile], \
                                             verbose, \
                                             io_group_asic_version_[iog], ref_current_trim, \
                                             tx_diff, tx_slice, r_term, i_rx,
                                             exclude=iog_exclude[iog],
                                             exclude_links=iog_exclude_links[iog])
                    out_of_network=network_base.iterate_waitlist(c, c.io, iog, \
                                                             utility_base.tile_to_io_channel([tile]),
                                                             verbose, \
                                                             io_group_asic_version_[iog], \
                                                             ref_current_trim,\
                                                             tx_diff, tx_slice, \
                                                             r_term, i_rx,
                                                             exclude=iog_exclude[iog],
                                                             exclude_links=iog_exclude_links[iog])
                    unconfigured.extend(out_of_network)
                output_prefix = _file_prefix or 'iog_{}-tile_{}'.format(iog, tile)
                print('ASIC discovery traversal finished; validating topology and export')
                network_file = network_base.write_network_to_file(c, output_prefix, {io_group : [tile] },\
                                       unconfigured, asic_version='2d')
                print(f'discovery complete: {len(unconfigured)} non-configured chips')
                print(f'topology validation and JSON export complete: {network_file}')

            return c, c.io

    

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pacman_config', required=True)
    parser.add_argument('--physical-root-map', action='append', default=None,
                        metavar='LOGICAL:POSITION',
                        help='operator-confirmed connector mapping; repeat for every live channel')
    parser.add_argument('--io_group', default=None, \
                        type=int, help='''io group to network''')
    parser.add_argument('--pacman_tile', default=None, \
                        type=int, help='''PACMAN tile to work with''') 
    parser.add_argument('--file_prefix', default=_default_file_prefix, \
                        type=str, help='''String prepended to filename''')
    parser.add_argument('--disable_logger', default=_default_disable_logger, \
                        action='store_true', help='''Disable logger''')
    parser.add_argument('--verbose', default=_default_verbose, \
                        action='store_true', help='''Enable verbose mode''')
    parser.add_argument('--ref_current_trim', \
                        default=_default_ref_current_trim, \
                        type=int, \
                        help='''Trim DAC for primary reference current''')
    parser.add_argument('--tx_diff', \
                        default=_default_tx_diff, \
                        type=int, \
                        help='''Differential per-slice loop current DAC''')
    parser.add_argument('--tx_slice', \
                        default=_default_tx_slice, \
                        type=int, \
                        help='''Slices enabled per transmitter DAC''')
    parser.add_argument('--r_term', \
                        default=_default_r_term, \
                        type=int, \
                        help='''Receiver termination DAC''')
    parser.add_argument('--i_rx', \
                        default=_default_i_rx, \
                        type=int, \
                        help='''Receiver bias current DAC''')
    args = parser.parse_args()
    main(**vars(args))
