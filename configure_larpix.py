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
from base.utility_base import now

_default_verbose=False

def main(verbose, \
        asic_config):
    
        db = pickledb.load(env_db, True) 

        #check DB file to ensure run conditions satisfy requirements
        for io_group in io_group_pacman_tile_.keys():

            #check that tiles are configured with hydra network
            TILES_CONFIGURED = db.get('IO_GROUP_{}_TILES_NETWORKED'.format(io_group))
            
            if not TILES_CONFIGURED:
                print('NO TILES  configured for io_group={}'.format(io_group))
                return

            if len(TILES_CONFIGURED)==0:
                print('NO TILES  configured for io_group={}'.format(io_group))
                return

            #check pacman is properly configured 
            PACMAN_CONFIGURED = db.get('IO_GROUP_{}_PACMAN_CONFIGURED'.format(io_group))
            if not PACMAN_CONFIGURED:
                print('PACMAN not configured for io_group={}'.format(io_group))
                return

            #check network config exists
            NETWORK_CONFIG =  db.get('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group))
            if not NETWORK_CONFIG:
                print('NO existing network file for io_group={}'.format(io_group))
                return

        c = larpix.Controller()
        c.io = larpix.io.PACMAN_IO(relaxed=True)
        
        #list of network keys in order from root chip, for parallel configuration enforcement
        all_network_keys = []
       
        for io_group in io_group_pacman_tile_.keys():
            CONFIG=None
            if asic_config is None:
                print('Using default config')
                CONFIG = db.get('DEFAULT_CONFIG_{}'.format(io_group))
            else:
                CONFIG=asic_config

            all_network_keys += enforce_parallel.get_chips_by_io_group_io_channel(db.get('IO_GROUP_{}_NETWORK_CONFIG'.format(io_group)) )  
            config_loader.load_config_from_directory(c, CONFIG) 
            
            #make the network keys io channel agnostic
            remove = [] 
            for i in range(len(all_network_keys)):
                for j in range(len(all_network_keys[i])):
                    if not all_network_keys[i][j] in c.chips:
                        
                        found=False
                        chip = all_network_keys[i][j]
                        tile =  utility_base.io_channel_to_tile(chip.io_channel)
                        io_channels = [ 1 + 4*(tile - 1) + n for n in range(4)]
                        
                        for io_channel in io_channels:
                            chip_key = '{}-{}-{}'.format(chip.io_group, io_channel, chip.chip_id)
                            if chip_key in c.chips:
                                all_network_keys[i][j] = chip_key
                                found=True
                                break

                        if not found:
                            remove.append(chip)


            for chip in remove:

                for i in range(len(all_network_keys)):
                    if chip in all_network_keys[i]:\
                            all_network_keys[i].remove(chip)




            db.set('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group), CONFIG)
            db.set('LAST_UPDATE', now()) 
        #ensure UARTs are enable on pacman to receive configuration packets
        for io_group in io_group_pacman_tile_.keys(): 
            pacman_base.enable_all_pacman_uart_from_io_group(c.io, io_group)

        #enforce all configurations in parallel (one chip per io channel per cycle)
        enforce_parallel.enforce_parallel(c, all_network_keys)

        return


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--asic_config', default=None, \
                        type=str, help='''ASIC config to load and enforce''')
    parser.add_argument('--verbose', '-v', action='store_true',  default=_default_verbose)
    args=parser.parse_args()
    c = main(**vars(args))

