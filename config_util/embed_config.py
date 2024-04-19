#!/usr/bin/env python3

import argparse
import io
from pathlib import Path
import tarfile

import numpy as np
import h5py


NAME = 'daq_configs'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('hdf5_file', type=Path)
    ap.add_argument('config_dir', type=Path)
    args = ap.parse_args()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='x:xz') as tarf:
        dirname = NAME + '.' + args.hdf5_file.stem
        tarf.add(args.config_dir, arcname=dirname)

    with h5py.File(args.hdf5_file, 'a') as h5f:
        data = np.frombuffer(buf.getvalue(), dtype=np.uint8)
        dset = h5f.create_dataset(NAME, data=data)
        # for convenience:
        dset.attrs['dirname'] = dirname


if __name__ == '__main__':
    main()
