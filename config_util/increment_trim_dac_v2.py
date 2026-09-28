import argparse
import json

def main(*files, inc=0, **kwargs):
    for file in files:
        with open(file, 'r') as f:
            config = json.load(f)

        for chan in range(64):
            # Skip only if the channel is masked AND its trim DAC is already 0
            if config['channel_mask'][chan] == 1 and config['pixel_trim_dac'][chan] == 0:
                continue

            config['pixel_trim_dac'][chan] = max(
                0,
                min(config['pixel_trim_dac'][chan] + inc, 31)
            )

        with open(file, 'w') as f:
            json.dump(config, f, indent=4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input_files', nargs='+', help='files to modify')
    parser.add_argument('--inc', type=int, default=0, help='amount to change trim threshold by')
    args = parser.parse_args()

    main(
        *args.input_files,
        inc=args.inc
    )
