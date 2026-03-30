import os
import json
import matplotlib.pyplot as plt
import argparse
import time


def main(asic_config):
    d = dict()

    for filename in os.listdir(asic_config):
        f = os.path.join(asic_config, filename)
        with open(f, 'r') as ff:
            d[filename.split('.')[0].split('_')[-1]] = json.load(ff)

    l = []
    max_threshold = 0
    for chip_key in d.keys():
        try:
            this_threshold = d[chip_key]['threshold_global']
            l.append(this_threshold)
            if this_threshold > max_threshold : max_threshold = this_threshold
        except:
            print(f"chip key {chip_key} not finding 'threshold_global' in {d[chip_key].keys()}")

    plt.hist(l, range(0,max_threshold+1))
    plt.grid()

    plt.xlabel('threshold_global')
    plt.ylabel('counts')

    now = time.strftime("%Y_%m_%d_%H_%M_%S_%Z")

    plt.savefig('threshold_global-'+now+'.png')
    print('Saved to: ', 'threshold_global-'+now+'.png')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--asic_config', default=None,
                        type=str, help='''configuration files per chip''')
    args = parser.parse_args()
    main(**vars(args))
