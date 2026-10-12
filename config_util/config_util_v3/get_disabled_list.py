import os
import argparse
import json
import time

def datetime_now():
	''' Return string with year, month, day, hour, minute '''
	return time.strftime("%Y_%m_%d_%H_%M_%Z")

def main(asic_config,
         **kwargs):

    disabled_list = {}
    
    timestamp=datetime_now()
    outfile='disabled_list-'+timestamp+'.json'
    disabled_list['meta']={ \
            outfile : {\
            'asic_directory' : asic_config,
            'created'        : timestamp,
            }
        }

    for filename in os.listdir(asic_config):
        config = {}
        file = os.path.join(asic_config, filename)
        with open(file, 'r') as f:
            config = json.load(f)
        chip_key = config['meta']['CHIP_KEY']
        csa_enable = config['csa_enable']
        disabled_channels = [chan for chan,val in enumerate(csa_enable) if val == 0]
        if len(disabled_channels) > 0: disabled_list[chip_key] = disabled_channels

    with open(outfile, 'w') as f:
        json.dump(disabled_list, f, indent=4)
    
if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--asic_config', default=None, type=str, help='''Input ASIC config directory''')

    args = parser.parse_args()
    main(**vars(args))
    