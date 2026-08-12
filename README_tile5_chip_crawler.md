# Tile 5 / LArPix-v2b chip-by-chip crawler

`tile5_chip_crawler.py` is an operator-driven Hydra diagnostic tool. It is intentionally **not** an automatic network discovery algorithm.

The experiment is:

1. Reset the ASICs.
2. Rebuild only the known-good rooted tree.
3. Expose exactly one neighboring candidate through one mother upstream PISO.
4. Repeatedly assign/configure the candidate while:
   - all 64 channels are masked;
   - all candidate upstream PISOs are disabled;
   - all candidate downstream PISOs are disabled.
5. Enable only the candidate downstream PISO that returns to its mother.
6. Unmask all 64 channels.
7. Stop and let the operator inspect the independent packet-rate/current/voltage monitoring.
8. Accept, expand again, roll back, or reconfigure.

No normal crawler step reads registers back. That is deliberate.

## First: rehearse in dry-run mode

From the `crs_daq` working directory:

```bash
python tile5_chip_crawler.py \
  --io-group 6 \
  --io-channel 12 \
  --root-chip 73 \
  --dry-run \
  --yes
```

Then try:

```text
tree
grid
map
expand 73 down
note packet rate looked normal
accept
expand 73 up
note current jumped after candidate went live
back
tree
quit
```

Dry-run creates the exact same session logs but never imports `larpix` or touches PACMAN.

## Real hardware example

Use the actual `io_channel`, physical root, and PACMAN tile for the network you intend to test:

```bash
python tile5_chip_crawler.py \
  --io-group 6 \
  --io-channel <IO_CHANNEL> \
  --root-chip <ROOT_CHIP> \
  --pacman-tile <PACMAN_TILE> \
  --pacman-config io/pacman_io6.json
```

The program shows the target and asks before the first reset unless `--yes` is supplied.

The crawler does **not** infer `pacman_tile` from the chip ID or io_channel.

## Main commands

- `status` — state summary
- `tree` — accepted rooted tree + current trial
- `grid` — 10x10 chip-ID map
- `map` — geometry/UART mapping
- `config` — pedestal values
- `expand <mother> <direction>` — reset/rebuild and test one new neighbor
- `accept` — mark the current trial known-good, with no hardware write
- `back` — remove the current trial (or last accepted edge), reset, and rebuild
- `reconfigure` — reset and replay the current topology
- `note <text>` — timestamp an operator observation
- `history [N]` — recent log events
- `safe-reset` — global reset only; do not rebuild
- `quit` — leave hardware unchanged and exit

Short aliases include `e`, `r`, `b`, and `q`.

## Branching

The internal topology is a rooted tree, not a chain.

For example, after:

```text
expand 73 down
accept
expand 73 up
accept
expand 83 right
```

the state can look like:

```text
73 [ROOT]
├── 63 [GOOD] (DOWN from 73)
└── 83 [GOOD] (UP from 73)
    └── 84 [TRIAL ?] (RIGHT from 83)
```

A chip may have multiple daughters, but a daughter may have only one mother. Loops are deliberately forbidden.

## UART mapping

Decoded from the 2x2 network JSON:

| daughter location | mother config TX | daughter config RX | daughter return TX | mother return RX |
|---|---:|---:|---:|---:|
| down | PISO3 | POSI2 | PISO1 | POSI0 |
| left | PISO0 | POSI3 | PISO2 | POSI1 |
| up | PISO1 | POSI0 | PISO3 | POSI2 |
| right | PISO2 | POSI1 | PISO0 | POSI3 |

## Pedestal configuration

The script uses the supplied Run-3-style values:

- `threshold_global = 255`
- `vcm_dac = 50`
- `vref_dac = 185`
- `enable_periodic_trigger = 1`
- `enable_rolling_periodic_trigger = 1`
- `enable_periodic_reset = 1`
- `enable_rolling_periodic_reset = 1`
- `enable_hit_veto = 0`
- `enable_periodic_trigger_veto = 0`
- `periodic_trigger_cycles = 578125`
- `periodic_reset_cycles = 4`
- `periodic_trigger_mask = [0]*64`
- `csa_enable = [1]*64`
- `tx_slices[0:4] = 15`
- `i_tx_diff[0:4] = 0`
- `r_term[0:4] = 8`
- `ref_current_trim = 0`

During blind configuration `channel_mask = [1]*64`. It becomes `[0]*64` only after the return PISO is enabled.

## Logs

Each run creates:

```text
crawler_logs/crawler_YYYYMMDD_HHMMSS_TZ/
    crawler.log
    events.jsonl
    session.json
    state.json
```

`events.jsonl` includes local and UTC timestamps, `epoch_ns`, `monotonic_ns`, elapsed time, operation sequence number, register writes, reset pulses, ID-claim attempts, link transitions, and operator notes.

Particularly useful event names for correlating against external plots:

- `RESET_BEGIN`
- `CANDIDATE_EXPOSED`
- `SAFE_CONFIG_ATTEMPT_BEGIN`
- `DOWNSTREAM_ENABLE_BEGIN`
- `UNMASK_BEGIN`
- `CHIP_LIVE`
- `TRIAL_HOLD`
- `OPERATOR_NOTE`

## Small crs_daq dependency

The Hydra/ASIC logic is implemented directly with `larpix-control`.

By default, if `base.pacman_base` is importable, the script uses it only for the two PACMAN-specific setup operations already used in 2x2:

- PACMAN UART inversion for the selected PACMAN tile;
- PACMAN UART RX enable for the selected io_channel.

Use `--no-crs-pacman-helpers` to skip both if PACMAN is already prepared externally.

## Important limitation

The crawler intentionally cannot assert that a configuration write "worked." It repeatedly writes the ID and desired configuration and then lets the external detector behavior answer the experimental question.

A Python call completing without an exception is **not** logged as a verified ASIC configuration.
