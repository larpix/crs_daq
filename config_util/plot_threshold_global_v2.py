import argparse
import json
import numpy as np
from matplotlib import pyplot as plt

def main(*files, inc=0, **kwargs):
        globs = []
        total=0
        for file in files:
                config={}
                with open(file, 'r') as f: config=json.load(f)
                
                try:
                    glob = config['threshold_global']
                    total+=1
                    if glob > 45: print(config['meta']['CHIP_KEY'],':', glob)
                except:
                    glob=255
                globs.append(glob)
        print(total)

        globs = np.array(globs)

        for val in sorted(set(globs)):
            print('{}: {}'.format(val, np.sum(globs == val)))

        # 255 is used above as the sentinel for configs without threshold_global
        valid_globs = globs[globs != 255]

        fig = plt.figure()
        ax = fig.add_subplot()

        bins = np.arange(valid_globs.min() - 0.5,
                         valid_globs.max() + 1.5,
                         1)

        ax.hist(valid_globs, bins=bins)

        ax.grid()
        ax.set_xlabel('global threshold DAC', fontsize=14)
        ax.set_ylabel('chip count', fontsize=14)

        fig.tight_layout()
        fig.savefig('threshold_global.png', dpi=150)
        return
                
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='''files to modify''')
    parser.add_argument('--inc', type=int, default=0, help='''amount to change global threshold by''')
    args = parser.parse_args()
    
    main(
        *args.input_files,
        inc=args.inc
    )
