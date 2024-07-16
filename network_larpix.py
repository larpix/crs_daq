import larpix
import time
import larpix.io
from RUNENV import *
import argparse
import pickledb
from base import config_loader
from base import network_base_FSD
from tqdm import tqdm
from base import pacman_base
from base import utility_base
from base import enforce_parallel
import json
from base.utility_base import now

_default_verbose = False
_default_controller_config = 'controller_config.json'

def enforce_iterative(nc, all_network_keys, n=0, configs=None):
    # print(n)
    ok, diff, unconfigured = enforce_parallel.enforce_parallel(nc, all_network_keys)
    if ok: 
        print(ok)
        return ok, diff, unconfigured
    elif n==0: 
        return ok, diff, unconfigured 
    else:
        all_keys = list(diff.keys())
        for net in unconfigured:
            all_keys += list(net)

        all_network_keys = []
        io_group_tiles = {}
        for chip_key in diff.keys():
            if not chip_key.io_group in io_group_tiles.keys(): io_group_tiles[chip_key.io_group] = set()
            io_group_tiles[chip_key.io_group].add(utility_base.io_channel_to_tile(chip_key.io_channel))  

        for chip_key in all_keys:
            if not chip_key.io_group in io_group_tiles: io_group_tiles[chip_key.io_group] = None

        for io_group in io_group_tiles.keys():
            tiles = io_group_tiles[io_group]
            config = configs[str(io_group)]
            if io_group_asic_version_[io_group] in ['2b', '2d']:
                c =  network_base_FSD.network_v2b(config, tiles=tiles, io_group=io_group)

            elif io_group_asic_version_[io_group] in [2, 'lightpix-1']:
                c = network_base_FSD.network_v2a(config, tiles=tiles, io_group=io_group)
           
            all_network_keys += enforce_parallel.get_chips_by_io_group_io_channel(config, use_keys=all_keys)

        return enforce_iterative(nc, all_network_keys, n=n-1, configs=configs)


def main(verbose,
         controller_config):

    db = pickledb.load(env_db, True)

    for io_group in io_group_pacman_tile_.keys():
        db.set('IO_GROUP_{}_TILES_NETWORKED'.format(io_group), [])
        db.set('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group), None)
        db.set('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group), None)
        db.set('LAST_UPDATED', now())
        db.set('DEFAULT_CONFIG_{}'.format(io_group), '')

        PACMAN_CONFIGURED = db.get(
            'IO_GROUP_{}_PACMAN_CONFIGURED'.format(io_group))
        if not PACMAN_CONFIGURED:
            print('PACMAN not configured for io_group={}'.format(io_group))
            return

    all_network_keys = []

    configs = {}
    with open(controller_config, 'r') as f:
        configs = json.load(f)

    # for each io_group, perform networking

    config_path = None
    for io_group in io_group_pacman_tile_.keys():

        print('Configuring io_group={}'.format(io_group))
        c = None

        config = configs[str(io_group)]

        if io_group_asic_version_[io_group] in ['2b', '2d']:
            c = network_base_FSD.network_v2b(config)

        elif io_group_asic_version_[io_group] in [2, 'lightpix-1']:
            c = network_base_FSD.network_v2a(config)

        all_network_keys += enforce_parallel.get_chips_by_io_group_io_channel(
            config)

        tiles = []
        for _, io_channels in c.network.items():
            tiles += utility_base.io_channel_list_to_tile(
                list(io_channels.keys()))

        db.set('IO_GROUP_{}_TILES_NETWORKED'.format(
            io_group), list(set([int(t) for t in tiles])))
        db.set('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group), config)
        if verbose:
            print('Setting {} to {}'.format('IO_GROUP_{}_ASIC_CONFIGS'.format(
                io_group), db.get('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group))))

        config_path = config_loader.write_config_to_file(c, config_path)
        db.set('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group), config_path)
        db.set('DEFAULT_CONFIG_{}'.format(io_group), config_path)
        db.set('LAST_UPDATED', now())

    nc = larpix.Controller()
    nc.io = larpix.io.PACMAN_IO(relaxed=True)

    for io_group in io_group_pacman_tile_.keys():
        pacman_base.enable_all_pacman_uart_from_io_group(nc.io, io_group)

    config_loader.load_config_from_directory(nc, config_path)
    # if True:
    #     enforce_parallel.enforce_parallel(nc, all_network_keys)
       # time.sleep(0.3)
    ok, diff, unconfigured = enforce_iterative(nc, all_network_keys, configs=configs)
    if not ok:
        raise RuntimeError('Unconfigured chips!', diff)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v',
                        action='store_true',  default=_default_verbose)
    parser.add_argument('--controller_config', default=_default_controller_config,
                        type=str, help='''Controller config specifying hydra network''')
    args = parser.parse_args()
    c = main(**vars(args))
