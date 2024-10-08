import larpix
import larpix.io
import argparse
import pickledb
from tqdm import tqdm
import os
import json


def main(input_files,
         **kwargs):

    for file in input_files:
        config = {}
        with open(file, 'r') as f:
            config = json.load(f)
        config['pixel_trim_dac'] = [16]*64

        with open(file, 'w') as f:
            json.dump(config, f, indent=4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')

    args = parser.parse_args()
    c = main(args.input_files)
