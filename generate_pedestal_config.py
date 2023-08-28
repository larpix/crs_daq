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
from base import asic_base
import os
_default_verbose=False

_default_periodic_trigger_cycles=200000
_default_periodic_reset_cycles=4096 #409600
_default_vref_dac=185 ###cold 223 ### warm 185
_default_vcm_dac=50 ### cold 68 ### warm 50
_default_ref_current_trim=0
_default_tx_diff=0
_default_tx_slice=15
_default_r_term=2
_default_i_rx=8


def main(verbose, \
         asic_config, \
         periodic_trigger_cycles=_default_periodic_trigger_cycles, \
         periodic_reset_cycles=_default_periodic_reset_cycles, \
         vref_dac=_default_vref_dac, \
         vcm_dac=_default_vcm_dac, \
         ref_current_trim=_default_ref_current_trim, \
         tx_diff=_default_tx_diff, \
         tx_slice=_default_tx_slice, \
         r_term=_default_r_term, \
         i_rx=_default_i_rx, \
         **kwargs):
        
    
        db = pickledb.load(env_db, True) 

        #check DB file to ensure run conditions satisfy requirements
        if asic_config is None:
            for io_group in io_group_pacman_tile_.keys():
                #check asic config exits 
                CONFIG = db.get('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group))

                if not CONFIG:
                    print('NO asic config known for io_group={}\nPlease specify config directory'.format(io_group))
                    return

                if not os.path.isdir(CONFIG):
                    print('NO asic config known for io_group={}\nPlease specify config directory'.format(io_group))
                    return

        c = larpix.Controller()
        c.io = larpix.io.PACMAN_IO(relaxed=True)
        
        for io_group in io_group_pacman_tile_.keys():
            CONFIG = db.get('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group))
            config_loader.load_config_from_directory(c, CONFIG) 
    

        #modify config here
        #note--does not change channel mask
        for io_group in io_group_pacman_tile_.keys():
            asic_base.enable_pedestal_config(c, io_group, \
                           vref_dac, vcm_dac, \
                           periodic_trigger_cycles, \
                           periodic_reset_cycles)

       
        
        config_path = config_loader.write_config_to_file(c, '{}/pedestal_config'.format(asic_config_dir) )
        print('!! Pedestal config files writen to {} !!'.format(config_path))

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--asic_config', default=None, \
                        type=str, help='''Base config to modify''')
    parser.add_argument('--verbose', default=_default_verbose, \
                        action='store_true', help='''Enable verbose mode''')
    parser.add_argument('--periodic_trigger_cycles', \
                        default=_default_periodic_trigger_cycles, type=int, \
                        help='''Periodic trigger cycles [MCLK]''')
    parser.add_argument('--periodic_reset_cycles', \
                        default=_default_periodic_reset_cycles, type=int, \
                        help='''Periodic reset cycles [MCLK]''')
    parser.add_argument('--vref_dac', default=_default_vref_dac, type=int, \
                        help='''Vref DAC''')
    parser.add_argument('--vcm_dac', default=_default_vcm_dac, type=int, \
                        help='''Vcm DAC''')
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

    args=parser.parse_args()
    c = main(**vars(args))
