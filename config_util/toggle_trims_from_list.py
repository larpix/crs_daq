import argparse
import json
import numpy as np
import os
from config_dtime import datetime_now

v2a_nonrouted_channels = [6,7,8,9,22,23,24,25,38,39,40,54,55,56,57]

def parse_toggle_json(toggle_json):

    if not os.path.isfile(toggle_json):
        raise RuntimeError('Toggle list does not exist')

    toggle_list = {}
    with open(toggle_json, 'r') as f: toggle_list=json.load(f)

    return toggle_list

def main(*files, toggle_json, **kwargs):
        toggle_list=parse_toggle_json(toggle_json)
        meta='unknown-toggle'
        if 'meta' in toggle_list.keys():
            meta=toggle_list['meta']
        disabled=0
        bottomed=0
        for file in files:
                config={}
                with open(file, 'r') as f: config=json.load(f)
                
                chip_key=config['meta']['ASIC_ID']
                version = config['meta']['ASIC_VERSION']

                if chip_key in toggle_list.keys():
                    if not 'pixel_trim_dac' in config.keys():
                        config['pixel_trim_dac']=[16]*64

                    for pair in toggle_list[chip_key]:
                        config['pixel_trim_dac'][pair[0]] += pair[1]
                        if config['pixel_trim_dac'][pair[0]] > 31:
                            config['pixel_trim_dac'][pair[0]] = 31
                            config['channel_mask'][pair[0]]=1
                            config['csa_enable'][pair[0]]=0
                            disabled+=1
                        if config['pixel_trim_dac'][pair[0]] < 0:
                            config['pixel_trim_dac'][pair[0]] = 0
                            bottomed+=1
                
                if 'meta' in config.keys():
                    config['meta']['last_update'] = datetime_now()
                    if not 'toggle_lists' in config['meta'].keys(): config['meta']['toggle_lists'] = []
                    config['meta']['toggle_lists'].append(meta)

                with open(file, 'w') as f: json.dump(config, f, indent=4)

        print('disabled {} channels'.format(disabled))
        print('bottomed out {} channels'.format(bottomed))
                
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--toggle_json', type=str, default=None, help='''List of (channel, toggle_value) to merge to config pixel trims''')
    args = parser.parse_args()
    
    main(
        *args.input_files,
        toggle_json=args.toggle_json
    )
