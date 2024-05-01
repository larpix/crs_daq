import argparse
import json
import numpy as np
from matplotlib import pyplot as plt

def main(*files, inc=0, **kwargs):
        trims = []
        for file in files:
                config={}
                with open(file, 'r') as f: config=json.load(f)
                
                ptd = np.array(config['pixel_trim_dac'])
                try:
                    csa = np.array(config['csa_enable'])
                    csa = np.logical_and( csa, np.logical_not(config['channel_mask']) )
                except:
                    csa=np.array([1]*64)
                trims += list( ptd[csa]  )


        vals, bins = np.histogram(trims, range=(-0.5, 31.5), bins=32)

        for ival, val in enumerate(vals):
            print('{}: {}'.format(ival, val))

        fig=plt.figure()
        ax=fig.add_subplot()
        ax.hist(trims,range=(-0.5, 31.5), bins=32 )
        ax.grid()
        ax.set_xlabel('pixel trim dac', fontsize=14)
        ax.set_ylabel('channel count', fontsize=14)
        fig.savefig('ptd.png')
                
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--inc', type=int, default=0, help='''amount to change global threshold by''')
    args = parser.parse_args()
    
    main(
        *args.input_files,
        inc=args.inc
    )
