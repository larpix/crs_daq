import logging
import json
from base.utility_base import now
from base import pacman_base
import time
import argparse
import larpix
import larpix.io

import datetime as dt

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

# urllib3 has functions to help with url timeout problems
import urllib3

import warnings

warnings.filterwarnings("ignore")

_default_verbose = False
skip_readback = False

ORG = "TBD"
TOKEN = "TBD"
URL = "TBD"
bucket = "pacmon"
measurement = "pacman_power"


def main(verbose, pacman_config):
    # sign into the lbl account
    # client = influxdb_client.InfluxDBClient(url=URL, token=TOKEN, org=ORG)

    # set up InfluxDB to accept data
    # write_api = client.write_api(write_options=SYNCHRONOUS)

    pacman_configs = {}
    with open(pacman_config, 'r') as f:
        pacman_configs = json.load(f)

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True, config_filepath=pacman_config)

    print(pacman_configs)

    # for each io_group, read power
    while True:
        print()
        print(dt.datetime.now())
        for io_group in range(1, 4):
            print('Looking at IO Group {}'.format(io_group))

            readback = pacman_base.power_readback(
                c.io, io_group, 'v1rev5', range(1, 11))

            # for t in readback.keys():
            #     p = influxdb_client.Point(measurement).tag("io_group", io_group).tag("tile", t).field("vdda",
            #                                                                                           float(readback[t][
            #                                                                                               0]))
            #     write_api.write(bucket=bucket, org=ORG, record=p)
            #     p = influxdb_client.Point(measurement).tag("io_group", io_group).tag("tile", t).field("idda",
            #                                                                                           float(readback[t][
            #                                                                                               1]) * rescale)
            #     write_api.write(bucket=bucket, org=ORG, record=p)
            #     p = influxdb_client.Point(measurement).tag("io_group", io_group).tag("tile", t).field("vddd",
            #                                                                                           float(readback[t][
            #                                                                                               2]))
            #     write_api.write(bucket=bucket, org=ORG, record=p)
            #     p = influxdb_client.Point(measurement).tag("io_group", io_group).tag("tile", t).field("iddd",
            #                                                                                           float(readback[t][
            #                                                                                               3]) * rescale)
            #     write_api.write(bucket=bucket, org=ORG, record=p)

        time.sleep(10)

    return


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pacman_config', default="/home/acd/acdaq/CRS_DAQ/daq0/crs_daq/io/pacman.json",
                        type=str, help='''Config specifying PACMANs''')
    parser.add_argument('--verbose', '-v', action='store_true',
                        default=_default_verbose)
    args = parser.parse_args()
    c = main(**vars(args))
