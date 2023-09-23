import argparse
import json
import numpy as np
import os

v2a_nonrouted_channels = [6,7,8,9,22,23,24,25,38,39,40,54,55,56,57]

def parse_disabled_json(disabled_json):

    if not os.path.isfile(disabled_json):
        raise RuntimeError('Disabled list does not exist')

    disabled_list = {}
    with open(disabled_json, 'r') as f: disabled_list=json.load(f)

    channel_masks = {}
    for key in disabled_list:
        channel_masks[key] = [1 if channel in disabled_list[key] else 0 for channel in range(64)]

    return channel_masks

def main(*files, disabled_json, **kwargs):
        channel_masks=parse_disabled_json(disabled_json)
        for file in files:
                config={}
                with open(file, 'r') as f: config=json.load(f)
                
                chip_key=config['ASIC_ID']
                version = config['ASIC_VERSION']

                if chip_key in channel_masks.keys():
                    _s = sum(config['channel_mask'])
                    mask = np.array(config['channel_mask'])+np.array(channel_masks[chip_key]) 
                    config['channel_mask'] = [1 if val>0 else 0 for val in mask]
                    if version==2: 
                        for channel in v2a_nonrouted_channels:
                            config['channel_mask'][channel]=1
                    print(chip_key, ': disabled', sum(config['channel_mask'])-_s, 'keys')
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
