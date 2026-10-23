#!/usr/bin/env python3
"""Inspect a CRS_DAQ raw PACMAN HDF5 file without connecting to hardware.

Defaults: IOG 1 = LArPix v2d (Packet_v2), IOG 2 = LArPix v3a (Packet_v3).
Requires only h5py. The parser implements the pinned larpix-control raw
PACMAN v0.0 message layout (8-byte header, 16-byte words).

Usage:
    python check_mixed_larpix_h5.py data/testing_v2d_and_v3.h5
    python check_mixed_larpix_h5.py data/new.h5 --require-both
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import struct
import sys

import h5py

# PACMAN message: header <cLxH; word: 16 bytes.
HEADER = struct.Struct('<cLxH')
WORD_SIZE = 16
FAMILIES = {1: 'v2d', 2: 'v3a'}
TRIGGERS = {0: 'normal', 1: 'external', 2: 'cross', 3: 'periodic'}
# Fast bit-parity lookup compatible with Python 3.9.
BYTE_PARITY = tuple(bin(value).count('1') & 1 for value in range(256))


def fmt_time(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def inspect(filename):
    stats = defaultdict(lambda: {
        'messages': 0,
        'asic_words': 0,
        'parity_ok': 0,
        'parity_bad': 0,
        'data': 0,
        'adc_nonzero': 0,
        'words': Counter(),
        'packet_types': Counter(),
        'valid_packet_types': Counter(),
        'sync_types': Counter(),
        'triggers': Counter(),
        'chip_counts': Counter(),
        'channel_counts': Counter(),
        'bad_payloads': Counter(),
        'header_times': [],
        'malformed_messages': 0,
    })

    with h5py.File(filename, 'r') as f:
        if 'msgs' not in f or 'msg_headers' not in f:
            raise ValueError('Expected raw CRS_DAQ datasets msgs and msg_headers')
        if 'io_groups' not in f['msg_headers'].dtype.names:
            raise ValueError('msg_headers must have an io_groups field')
        if len(f['msgs']) != len(f['msg_headers']):
            raise ValueError('msgs and msg_headers have different lengths')

        meta = f['meta'].attrs if 'meta' in f else {}
        created = meta.get('created')
        modified = meta.get('modified')
        n_messages = len(f['msgs'])
        print(f'File: {filename}')
        print(f'Raw messages: {n_messages:,}')
        if created is not None and modified is not None:
            print(f'Recording: {fmt_time(created)} -> {fmt_time(modified)} '
                  f'({modified - created:.1f} s)')
        print(f'Raw IO version: {meta.get("io_version", "unknown")}')

        # Process chunks instead of materializing the entire HDF5 file in RAM.
        for start in range(0, n_messages, 1024):
            stop = min(start + 1024, n_messages)
            groups = f['msg_headers']['io_groups'][start:stop]
            for group, array in zip(groups, f['msgs'][start:stop]):
                iog = int(group)
                s = stats[iog]
                s['messages'] += 1
                msg = bytes(array)
                if len(msg) < HEADER.size:
                    s['malformed_messages'] += 1
                    continue

                kind, unix_time, nwords = HEADER.unpack_from(msg)
                if kind != b'D' or len(msg) != HEADER.size + nwords * WORD_SIZE:
                    s['malformed_messages'] += 1
                    continue
                if not s['header_times']:
                    s['header_times'].append(unix_time)
                elif len(s['header_times']) == 1:
                    s['header_times'].append(unix_time)
                else:
                    s['header_times'][-1] = unix_time

                family = FAMILIES.get(iog)
                for pos in range(HEADER.size, len(msg), WORD_SIZE):
                    word = msg[pos:pos + WORD_SIZE]
                    word_type = chr(word[0])
                    s['words'][word_type] += 1
                    if word_type == 'S':
                        s['sync_types'][word[1]] += 1
                    if word_type != 'D':
                        continue

                    s['asic_words'] += 1
                    payload = word[8:16]
                    bits = int.from_bytes(payload, 'little')
                    ptype = bits & 0x3
                    s['packet_types'][ptype] += 1

                    # larpix Packet_v2/v3 use odd total 64-bit parity.
                    if not (sum(BYTE_PARITY[byte] for byte in payload) & 1):
                        s['parity_bad'] += 1
                        s['bad_payloads'][(word[1], payload.hex())] += 1
                        continue
                    s['parity_ok'] += 1
                    s['valid_packet_types'][ptype] += 1

                    # v2 DATA type = 0; v3 DATA type = 1.
                    if family not in ('v2d', 'v3a'):
                        continue
                    data_type = 0 if family == 'v2d' else 1
                    if ptype != data_type:
                        continue

                    s['data'] += 1
                    chip_id = (bits >> 2) & 0xff
                    io_channel = int(word[1])
                    s['chip_counts'][(io_channel, chip_id)] += 1
                    s['channel_counts'][io_channel] += 1
                    s['triggers'][TRIGGERS[(bits >> 56) & 3]] += 1
                    adc = (bits >> 48) & 0xff if family == 'v2d' else (bits >> 46) & 0x3ff
                    if adc:
                        s['adc_nonzero'] += 1

    for iog, s in sorted(stats.items()):
        print(f'\n--- IOGroup {iog} ({FAMILIES.get(iog, "unknown ASIC family")}) ---')
        print(f'PACMAN messages:      {s["messages"]:,}')
        print(f'PACMAN word types:    {dict(sorted(s["words"].items()))}')
        print(f'SYNC types:           {dict(sorted(s["sync_types"].items()))}')
        print(f'ASIC payload words:   {s["asic_words"]:,}')
        print(f'Parity valid/invalid: {s["parity_ok"]:,} / {s["parity_bad"]:,}')
        print(f'Valid packet types:   {dict(sorted(s["valid_packet_types"].items()))}')
        print(f'Valid DATA packets:   {s["data"]:,}')
        print(f'ASICs with data:      {len(s["chip_counts"]):,}')
        print(f'DATA trigger types:   {dict(sorted(s["triggers"].items()))}')
        if s['data']:
            print(f'Nonzero ADC packets:  {s["adc_nonzero"]:,} '
                  f'({100*s["adc_nonzero"]/s["data"]:.2f}%)')
            for ch, count in sorted(s['channel_counts'].items()):
                n_chips = sum(1 for c, _ in s['chip_counts'] if c == ch)
                print(f'  IO channel {ch}: {count:,} DATA packets from {n_chips} ASICs')
        if s['bad_payloads']:
            print('Most common invalid payloads (IO channel, hex, count):')
            for (ch, payload), count in s['bad_payloads'].most_common(3):
                print(f'  {ch:2d}: {payload} x {count:,}')
        if s['header_times']:
            first, last = s['header_times'][0], s['header_times'][-1]
            print(f'PACMAN header times:  {fmt_time(first)} -> {fmt_time(last)}')
        if s['malformed_messages']:
            print(f'WARNING: {s["malformed_messages"]} malformed PACMAN messages')

    both = all(stats[iog]['data'] > 0 for iog in FAMILIES)
    print('\nMIXED DATA:', 'PASS: valid DATA packets from both families' if both
          else 'NOT YET: no valid DATA packets from ' +
               ', '.join(f'IOG{iog}' for iog in FAMILIES if stats[iog]['data'] == 0))
    return both


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', help='CRS_DAQ raw .h5 file (not parsed packet HDF5)')
    parser.add_argument('--require-both', action='store_true',
                        help='exit with code 2 unless both IOG1/v2d and IOG2/v3a have valid DATA packets')
    args = parser.parse_args()
    both = inspect(args.file)
    if args.require_both and not both:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
