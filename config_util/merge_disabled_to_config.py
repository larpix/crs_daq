import argparse
from base import config_loader
import json
import numpy as np

def main(*files, disabled_json, **kwargs):
        channel_masks=config_loader.parse_disabled_json(disabled_json)
        for file in files:
                config={}
                with open(file, 'r') as f: config=json.load(f)
                
                chip_key=config['CHIP_KEY']
                
                if chip_key in channel_masks.keys():
                    _s = sum(config['channel_mask'])
                    mask = np.array(config['channel_mask'])+np.array(channel_masks[chip_key])
                    config['channel_mask'] = [1 if val>0 else 0 for val in mask]
                    print('disabled', sum(config['channel_mask'])-_s, 'keys')
                    config['csa_enable']=[1 if val==0 else 0 for val in channel_masks[chip_key]]

                with open(file, 'w') as f: json.dump(config, f, indent=4)

                
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--disabled_json', type=str, default=None, help='''Disabled list to merge to config''')
    args = parser.parse_args()
    
    main(
        *args.input_files,
        disabled_json=args.disabled_json
    )
