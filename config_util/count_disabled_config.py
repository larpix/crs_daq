import argparse
from base import config_loader
import json
import numpy as np

def main(*files, disabled_json, **kwargs):
        total=0
        for file in files:
                config={}
                with open(file, 'r') as f: config=json.load(f)
                
                chip_key=config['CHIP_KEY']
                
                _s = sum(config['channel_mask'])
                total+=_s
        total = total - (64-49)*1600
        print('total: ', total)
        print('fraction: ', (total)/(49*1600))
                
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--disabled_json', type=str, default=None, help='''Disabled list to merge to config''')
    args = parser.parse_args()
    
    main(
        *args.input_files,
        disabled_json=args.disabled_json
    )
