import time
import larpix
import larpix.io
from RUNENV import *
import argparse
import pickledb
from base import config_loader
from base import network_base
from tqdm import tqdm
from base import pacman_base
from base import utility_base
from base import enforce_parallel

_default_verbose = False
_default_controller_config = None

def main(verbose,\
        controller_config):
    
    db = pickledb.load(env_db, True) 

    for io_group in io_group_pacman_tile_.keys():
        db.set('IO_GROUP_{}_TILES_NETWORKED'.format(io_group), [])
        db.set('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group), None)
        db.set('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group), None)

    for io_group in io_group_pacman_tile_.keys():
        PACMAN_CONFIGURED = db.get('IO_GROUP_{}_PACMAN_CONFIGURED'.format(io_group))
        if not PACMAN_CONFIGURED:
            raise RuntimeError('PACMAN not configured for io_group={}'.format(io_group))

    
    all_network_keys = []
  
    # for each io_group, perform networking
    for io_group in io_group_pacman_tile_.keys():
        
        c = None
 
        if io_group_asic_version_[io_group]=='2b':
            c =  network_base.network_v2b(controller_config)
        
        elif io_group_asic_version_[io_group]==2:
            c = network_base.network_v2a(controller_config)
        
        all_network_keys += enforce_parallel.get_chips_by_io_group_io_channel(controller_config)

        tiles = []
        for _, io_channels in c.network.items(): tiles += utility_base.io_channel_list_to_tile(list(io_channels.keys()) )
        
        db.set('IO_GROUP_{}_TILES_NETWORKED'.format(io_group), list(set([int(t) for t in tiles] )) )
        db.set('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group), controller_config)
        if verbose: print('Setting {} to {}'.format('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group), db.get('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group))))
        
        config_path = config_loader.write_config_to_file(c) 
        db.set('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group), config_path)        
        

    for io_group in io_group_pacman_tile_.keys(): 
        pacman_base.enable_all_pacman_uart_from_io_group(c.io, io_group)
   
    enforce_parallel.enforce_parallel(c, all_network_keys) 

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v', action='store_true',  default=_default_verbose)
    parser.add_argument('--controller_config', default=_default_controller_config, \
                        type=str, help='''Controller config specifying hydra network''')                  
    args=parser.parse_args()
    c = main(**vars(args))

