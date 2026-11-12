#!/usr/bin/env python3

import argparse
import datetime as dt
import os
from pathlib import Path
import json
from subprocess import run
import time

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

ORG = "influxdata"
TOKEN = os.getenv("INFLUXDB_TOKEN")
URL = "http://np04-srv-017:32700"
BUCKET = "pacmon"
MEASUREMENT = "pacman_file_size"


def main(run_config: Path):
    client = influxdb_client.InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    with open(run_config, "r") as f:
        config = json.load(f)
    destination_dir = config["destination_dir_"]
    fullpath = lambda f: os.path.join(destination_dir, f)

    while True:
        print(f"\n{dt.datetime.now()}")
        files = [
            fullpath(f)
            for f in os.listdir(destination_dir)
            if os.path.isfile(fullpath(f))
        ]
        latest_file = max(files, key=os.path.getmtime)
        size_bytes = os.path.getsize(latest_file)
        p = influxdb_client.Point(MEASUREMENT).field("size_bytes", size_bytes)
        write_api.write(bucket=BUCKET, org=ORG, record=p)
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run_config",
        default="RUN_CONFIG.json",
        type=Path,
        help="""Path of RUN_CONFIG.json""",
    )
    args = parser.parse_args()
    main(**vars(args))
