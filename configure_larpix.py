import larpix
import larpix.io
import argparse
from base import config_loader
from RUNENV import *
import pickledb
from tqdm import tqdm
from base import pacman_base
from base import utility_base
from base import enforce_parallel

_default_verbose=False

def main(verbose, \
        asic_config):
    
        db = pickledb.load(env_db, True) 

        for io_group in io_group_pacman_tile_.keys():
            TILES_CONFIGURED = db.get('IO_GROUP_{}_TILES_NETWORKED'.format(io_group))
            if not TILES_CONFIGURED:
                raise RuntimeError('NO TILES  configured for io_group={}'.format(io_group))
            if len(TILES_CONFIGURED)==0:
                raise RuntimeError('NO TILES  configured for io_group={}'.format(io_group))

        for io_group in io_group_pacman_tile_.keys():
            PACMAN_CONFIGURED = db.get('IO_GROUP_{}_PACMAN_CONFIGURED'.format(io_group))
            if not PACMAN_CONFIGURED:
                raise RuntimeError('PACMAN not configured for io_group={}'.format(io_group))

        for io_group in io_group_pacman_tile_.keys():
            NETWORK_CONFIG =  db.get('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group))
            if not NETWORK_CONFIG:
                raise RuntimeError('NO existing network file for io_group={}'.format(io_group))

        c = larpix.Controller()
        c.io = larpix.io.PACMAN_IO(relaxed=True)
        
        all_network_keys = []
       
        for io_group in io_group_pacman_tile_.keys():
            CONFIG = db.get('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group))
            all_network_keys += enforce_parallel.get_chips_by_io_group_io_channel(db.get('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group)) )  
            config_loader.load_config_from_directory(c, CONFIG) 
        
        for io_group in io_group_pacman_tile_.keys(): 
            pacman_base.enable_all_pacman_uart_from_io_group(c.io, io_group)
    

        enforce_parallel.enforce_parallel(c, all_network_keys)

        return


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--asic_config', default=None, \
                        type=str, help='''ASIC config to load and enforce''')
    parser.add_argument('--verbose', '-v', action='store_true',  default=_default_verbose)
    args=parser.parse_args()
    c = main(**vars(args))

