#!/usr/bin/env python3

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


def load_pacmans(json_path):
    with open(json_path, "r") as f:
        data = json.load(f)

    pacmans = []
    for entry in data["io_group"]:
        io_group, ip = entry
        pacmans.append((io_group, ip))

    return pacmans


def ping_one(io_group, ip, timeout_s=1):
    """
    Linux ping:
      -n       numeric output only
      -c 1     send one packet
      -W 1     wait up to 1 second
    """
    cmd = ["ping", "-n", "-c", "1", "-W", str(timeout_s), ip]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s + 0.5,
        )

        up = result.returncode == 0

        latency = None
        if up:
            for line in result.stdout.splitlines():
                if "time=" in line:
                    latency = line.split("time=")[1].split()[0]
                    break

        return {
            "io_group": io_group,
            "ip": ip,
            "up": up,
            "latency_ms": latency,
        }

    except subprocess.TimeoutExpired:
        return {
            "io_group": io_group,
            "ip": ip,
            "up": False,
            "latency_ms": None,
        }


def print_status(results):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("\033[2J\033[H", end="")  # clear terminal screen
    print(f"PACMAN ping status - {now}")
    print("-" * 55)
    print(f"{'io_group':>8}  {'IP':>16}  {'status':>8}  {'latency'}")
    print("-" * 55)

    for r in sorted(results, key=lambda x: x["io_group"]):
        status = "UP" if r["up"] else "DOWN"
        latency = f"{r['latency_ms']} ms" if r["latency_ms"] is not None else "-"
        print(f"{r['io_group']:>8}  {r['ip']:>16}  {status:>8}  {latency}")

    print("-" * 55)
    print("Ctrl-C to stop")


def main():
    parser = argparse.ArgumentParser(description="Continuously ping PACMANs from pacman.json")
    parser.add_argument(
        "json_path",
        nargs="?",
        default="io/pacman.json",
        help="Path to PACMAN JSON file, default: io/pacman.json",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between ping rounds, default: 1.0",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1,
        help="Ping timeout in seconds, default: 1",
    )

    args = parser.parse_args()

    pacmans = load_pacmans(args.json_path)

    print(f"Loaded {len(pacmans)} PACMANs from {args.json_path}")

    while True:
        start = time.time()
        results = []

        with ThreadPoolExecutor(max_workers=len(pacmans)) as executor:
            futures = [
                executor.submit(ping_one, io_group, ip, args.timeout)
                for io_group, ip in pacmans
            ]

            for future in as_completed(futures):
                results.append(future.result())

        print_status(results)

        elapsed = time.time() - start
        sleep_time = max(0.0, args.interval - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
