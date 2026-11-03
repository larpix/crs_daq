#!/usr/bin/env python3

import argparse
import datetime as dt
import os
import json
from subprocess import run
import time


import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

ORG = "influxdata"
TOKEN = os.getenv("INFLUXDB_TOKEN")
URL = "http://np04-srv-017:32700"
BUCKET = "pacmon"
MEASUREMENT = "pacman_temperature"

SSH_CMD = (
    "OPENSSL_CONF=~/pCRP/pacmon/openssl-sha1.cnf sshpass -f ~/pCRP/.pacman_login ssh"
)

CPU_TEMP_FILE = "/sys/bus/iio/devices/iio:device0/in_temp0_raw"


def read_temp(ip: str) -> int:
    cmd = f"{SSH_CMD} root@{ip} cat {CPU_TEMP_FILE}"
    result = run(cmd, shell=True, capture_output=True, text=True, check=False)
    return float(result.stdout) / 8.127 - 273.15


def main(pacman_config: str):
    client = influxdb_client.InfluxDBClient(url=URL, token=TOKEN, org=ORG)

    # set up InfluxDB to accept data
    write_api = client.write_api(write_options=SYNCHRONOUS)

    with open(pacman_config, "r") as f:
        pacman_configs = json.load(f)

    while True:
        print()
        print(dt.datetime.now())
        for io_group, ip in pacman_configs["io_group"]:
            print(f"Looking at IO Group {io_group}")
            temp = read_temp(ip)
            p = (
                influxdb_client.Point(MEASUREMENT)
                .tag("io_group", io_group)
                .field("temp", temp)
            )
            write_api.write(bucket=BUCKET, org=ORG, record=p)
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pacman_config",
        default="io/pacman.json",
        type=str,
        help="""Config specifying PACMANs""",
    )
    args = parser.parse_args()
    main(**vars(args))
