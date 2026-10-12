import warnings
warnings.filterwarnings("ignore")

import larpix
import larpix.io
import argparse
from tqdm import tqdm
import os
import json
from config_dtime import datetime_now
import numpy as np

hydra_registers_v2b = ['enable_piso_downstream', 'enable_piso_upstream', 'enable_posi']
hydra_registers_v2a = ['enable_miso_downstream', 'enable_miso_upstream', 'enable_mosi']

def io_channel_to_tile(io_channel):
    return int(np.floor((io_channel-1-((io_channel-1)%4))/4+1))

def get_asic_id(chip):
    return '{}-{}-{}'.format(chip.io_group, io_channel_to_tile(chip.io_channel), chip.chip_id)

def main(input_files, controller_config, \
         **kwargs):
        
        configs = {}
        with open(controller_config, 'r') as f:
            configs = json.load(f)
        
        #Take inventory of all chips in hydra network
        chip_keys=[]
        asic_ids =[]
        used_ids =[]
        for file in input_files:
            asic_config={}
            with open(file, 'r') as f: asic_config=json.load(f)

            _chip_key=None
            _asic_id =None
            if 'meta' in asic_config.keys():
                _chip_key=asic_config['meta']['CHIP_KEY']
                _asic_id=asic_config['meta']['ASIC_ID']
            else:
                _chip_key=asic_config['CHIP_KEY']
                _asic_id=asic_config['ASIC_ID']

            chip_keys.append(_chip_key)
            asic_ids.append(_asic_id)
        
        c = larpix.Controller()
        c.io = larpix.io.FakeIO(print_packets=False)

        for key in configs.keys():
            c.load(configs[key])

            for io_group, io_channels in c.network.items():
                for io_channel in io_channels:
                    c.init_network(io_group, io_channel, modify_mosi=False)
           
            test_chip = list(c.chips.keys())[0]
            hydra_registers=None
            if c[test_chip].asic_version==2:
                hydra_registers=hydra_registers_v2a
            elif c[test_chip].asic_version=='2b':
                hydra_registers=hydra_registers_v2b
            
            for chip in c.chips:
                _asic_id = get_asic_id(chip)
                if _asic_id in asic_ids:
                    #found the chip we need to modify
                    index=asic_ids.index(_asic_id)
                    used_ids.append(_asic_id)
                    asic_config={}
                    file=input_files[index]
                    with open(file, 'r') as f: asic_config=json.load(f)

                    asic_config['meta']['CHIP_KEY']=chip_keys[index]
                    for register in hydra_registers: 
                        asic_config[register] = getattr( c[chip].config, register ) 

                    asic_config['meta']['last_update'] = datetime_now()

                    with open(file, 'w') as f: json.dump(asic_config, f, indent=4)

        print('Remove files:', set(asic_ids)-set(used_ids)) 
     
if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--controller_config', default='configs/controller_config.json', \
                        type=str, help='''Controller config for hydra networks''')

    args=parser.parse_args()
    c = main(args.input_files, controller_config=args.controller_config)
