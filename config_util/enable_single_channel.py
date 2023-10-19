import larpix
import larpix.io
import argparse
import pickledb
from tqdm import tqdm
import os
import json


def main(input_files, channel, \
         **kwargs):
        for file in input_files:
            config={}
            with open(file, 'r') as f: config=json.load(f)
            config['channel_mask']=[1]*64
            config['channel_mask'][channel]=0

            config['csa_enable']=[0]*64
            config['csa_enable'][channel]=1


            with open(file, 'w') as f: json.dump(config, f, indent=4)
                           
if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--channel', \
                        default=None, type=int, \
                        help='''Channel to enable''')
    
    args=parser.parse_args()
    c = main(args.input_files, \
            register=args.register, \
            value   =args.value)
