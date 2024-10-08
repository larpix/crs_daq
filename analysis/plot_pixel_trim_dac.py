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
    for chip_key in d.keys():
        [l.append(v) for v in d[chip_key]['pixel_trim_dac']]
    plt.hist(l, bins=30, range=[0, 31])
    plt.xlabel('pixel_trim_dac')
    plt.ylabel('counts')
    plt.yscale('log')

    now = time.strftime("%Y_%m_%d_%H_%M_%S_%Z")

    plt.savefig('pixel_trim_dac-'+now+'.png')
    print('Saved to: ', 'pixel_trim_dac-'+now+'.png')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--asic_config', default=None,
                        type=str, help='''HDF5 fielname''')
    args = parser.parse_args()
    main(**vars(args))
