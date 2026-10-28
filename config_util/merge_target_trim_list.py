import argparse
import json
import numpy as np
import os
import time


def datetime_now():
    ''' Return string with year, month, day, hour, minute, second '''
    return time.strftime("%Y_%m_%d_%H_%M_%S_%Z")


def parse_target_json(target_json):

    if not os.path.isfile(target_json):
        raise RuntimeError('Toggle list does not exist')

    trim_target_list = {}
    with open(target_json, 'r') as f:
        trim_target_list = json.load(f)

    return trim_target_list


def main(*files, target_json, **kwargs):

    trim_target_list = parse_target_json(target_json)
    meta = 'unknown-toggle'
    if 'meta' in trim_target_list.keys():
        meta = trim_target_list['meta']
 
    for file in files:
        config = {}
        with open(file, 'r') as f:
            config = json.load(f)

        chip_key = file.split('_')[-1].split('.')[0]
        if chip_key in trim_target_list.keys():

            #Explicitly establish default if not present already
            if not 'pixel_trim_dac' in config.keys():
                config['pixel_trim_dac'] = [16]*64

            for channel, trim in trim_target_list[chip_key]:

                #Set targeted trim if available
                if not np.isnan(trim): config['pixel_trim_dac'][channel] = int(trim)

                #Disable if unable to set target threshold
                else:
                    config['channel_mask'][channel] = 1
                    config['csa_enable'][channel] = 0

        #Max out trim of all disabled channels for book-keeping
        for channel in range(64):
            if config['csa_enable'][channel] == 0:
                config['pixel_trim_dac'][channel] = 31

        if 'meta' in config.keys():
            config['meta']['last_update'] = datetime_now()
            if not 'trim_target_lists' in config['meta'].keys():
                config['meta']['trim_target_lists'] = []
            config['meta']['trim_target_lists'].append(meta)

        with open(file, 'w') as f:
            json.dump(config, f, indent=4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--target_json', type=str, default=None, help='''List of trim values to merge to config''')
    args = parser.parse_args()

    main(
        *args.input_files,
        target_json=args.target_json
    )
