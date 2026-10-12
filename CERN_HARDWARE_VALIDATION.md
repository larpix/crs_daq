# CERN control/configuration validation

## Authorization and scope

Every command in the hardware stages below requires separate explicit operator
approval. None was executed during the donor restoration. Approval must include
the selected IOG/tile, exact commands, expected effects and recovery command.
Run from `/home/bgelli/cern-coldbox/worktrees/crs-cern`. Stop on a failed stage;
do not insert a scratch bootstrap to make the next stage work.

| IOG | Endpoint | Family / packet family | Powered tile | Live logical channels / roots |
|---|---|---|---|---|
| 1 | `192.168.0.104` (PACMAN58) | `"2d"` / `2` | 7 | 25/21, 26/61, 27/101; 28 unavailable |
| 2 | `192.168.0.100` (PACMAN57) | `3` / `3` | 10 | 37/151, 38/111, 39/61; 40 unavailable |

Use one-PACMAN `io/pacman_ioN.json` for control. Never substitute aggregate
`io/pacman.json`. Preserve the local `.104`/`.100` endpoints. Power-up disables
all tile outputs on that PACMAN before enabling the configured tile; recovery
power-down disables all ten outputs on that PACMAN. The other PACMAN is outside
the operation's scope.

The baseline source/runtime snapshot is
`/tmp/cern-preservation-l9i87a7m/cern-state.tar.gz` with SHA-256 manifest beside
it. Preserve a new snapshot of hidden indexes, selected ASIC directories,
connection files and topologies immediately before any supervised run. Keep
new outputs under fresh names. Do not delete existing files or restore a Git
checkout over runtime configuration.

## What is actually established

Supplied LOG5/LOG6 show complete v2d discovery (zero outstanding chips) and a
successful saved-network replay. They do not show complete operating
configuration/readback after this repair. V3 logs show roots on channels
37/38/39 responding, then a channel40 software exception, not full discovery.
The earlier socket-only connectivity test established TCP reachability only.

Current live files still select a v3 root-only topology requesting `2-39-1`
and an ASIC directory containing `2-37-1`. They are preserved unchanged. Normal
networking now rejects that mismatch before opening hardware IO. Do not use
those files as evidence of full tile discovery, or regenerate defaults against
that one-root topology as a substitute for discovery.

Offline tests use the actual installed larpix-control at
`5a69050422e82356c8faf9ad0ea3168c322d63e8`. They execute real configuration
serialization, graph loading, full v3 traversal/export/replay, and mocked
reconstruction/lifecycle tests with sockets blocked. They cannot establish
voltages, timings, firmware reset semantics or hardware readback success.

## Offline check (permitted without hardware approval)

```bash
python -B tests/run_offline.py
```

This creates temporary runtime state, blocks PACMAN_IO construction except
explicit test fakes, blocks socket connections/ZMQ contexts, and treats skipped
tests as a failing gate. It does not run the repository's unrelated operational
`test_trigger_rate.py`. Do not invoke a broad discovery command over operational
scripts or import `runenv` in an unprepared working directory for inspection.

## Stage 1 — supervised discovery, separately for each board

First obtain approval for IOG1/tile7. Choose a fresh stamp; keep all output logs.
These shell variables/log directories are filesystem operations, but the Python
and power commands are hardware operations.

```bash
validation_stamp=$(date +%Y_%m_%d_%H_%M_%S_%Z)
mkdir -p log
bash run/iog/power_up.sh 1 >"log/validation-power-iog1-${validation_stamp}.log" 2>&1
python -u hydra_v2d.py --io_group 1 --pacman_tile 7 \
  --pacman_config io/pacman_io1.json --verbose \
  --file_prefix "validation-iog1-tile7-${validation_stamp}" \
  >"log/validation-discovery-iog1-${validation_stamp}.log" 2>&1
```

Expected effects: reprogram DACs/tile power and PACMAN infrastructure, hard-reset
ASICs, assign donor IDs, discover links and read back configuration. Expect
roots 21/61/101, channels 25–27, 160 ASICs unless exclusions were independently
approved, zero final undiscovered ASICs, and a fresh validated topology. Preserve
root101; physical-position relabeling is outside this task. Compare the new
loaded UART-labelled graph and operating settings to the preserved fixture.
Do not promote output solely because the final line says export succeeded.

Then obtain approval for IOG2/tile10 and run:

```bash
bash run/iog/power_up.sh 2 >"log/validation-power-iog2-${validation_stamp}.log" 2>&1
python -u hydra_v3.py --io_group 2 --pacman_tile 10 \
  --pacman_config io/pacman_io2.json --verbose \
  --file_prefix "validation-iog2-tile10-${validation_stamp}" \
  >"log/validation-discovery-iog2-${validation_stamp}.log" 2>&1
```

Expected effects match IOG1 but with v3 register classes/packet family. Expect
roots 151/111/61 on channels 37/38/39, deliberate omission of unavailable 40,
full initial traversal and waitlist recovery, and 160 discovered ASICs for the
current empty exclusion lists. A live root failure or missing final chip causes
a nonzero exit without publishing a topology. `--io-channel` root-only mode is
no longer an operational interface. Existing output names are refused.

Inspect each log and stop after a failure. The donor v3 signal defaults are
preserved (v_cm_lvds_tx=5, tx_diff=7, tx_slice=15, r_term=7, i_rx=0 at discovery;
replay's inherited i_rx=3 remains). Their behavior on real Rev5 hardware is a
bench gate, not an offline conclusion.

## Stage 2 — select reviewed full topologies (filesystem operation)

After successful discovery and review, preserve the old
`configs/controller_config.json`, then update its existing `"1"` and `"2"`
values to the two exact fresh `*-hydra-network.json` paths above. Review that
small file diff before hardware replay. Do not overwrite the generated topology
files, manually edit ASIC IDs, delete defaults or copy a diagnostic JSON into
place. This restoration itself leaves all deployed selections untouched.

## Stage 3 — decisive fresh-start replay and configure gate

Obtain approval for each IOG separately, with the exact topology/ASIC paths in
the review. For each board the sequence must be fresh power/reset -> normal
PACMAN setup -> saved-network replay -> normal configuration/readback. No
scratch root bootstrap may occur between these steps. A controlled power cycle
through the existing entry points is:

```bash
bash run/iog/power_down.sh 1
bash run/iog/power_up.sh 1
bash run/iog/network.sh 1 --update_default
bash run/iog/configure.sh 1
```

After reviewing IOG1's result, obtain approval for the corresponding IOG2 gate:

```bash
bash run/iog/power_down.sh 2
bash run/iog/power_up.sh 2
bash run/iog/network.sh 2 --update_default
bash run/iog/configure.sh 2
```

Run sequentially and stop on any failure; do not paste the entire sequence into
an unattended shell. Power-up keeps the deployed electrical order, DAC values,
4096-cycle reset, 0.5-second settling delay, RX mask, sync mask `0x3ff`, 32 packet
delays `0xff` and persistent UART ratios (v2d 2; v3 1). Networking establishes
those communication settings again and applies the donor replay sequence.

Explicit refresh is appropriate here only after the newly discovered full
topology has been reviewed. It writes to a fresh timestamped `iogN` directory
and preserves prior defaults/config files. A log names the generated or reused
path. Current/default indexes are selected only after success; failure leaves
current selections invalid and the previous saved default available. Existing
output directories are refused, even if empty; use a fresh path/stamp.

Configuration uses the current index by default. The wrapper's optional path is
a **parent** containing `iog1`/`iog2` subdirectories:

```bash
bash run/iog/configure.sh 1 asic_configs/REVIEWED_PARENT
```

The Python entry point also accepts a **leaf** without `--config_subdir`:

```bash
python configure_larpix.py --pacman_config io/pacman_io1.json \
  --asic_config asic_configs/REVIEWED_PARENT/iog1 --verbose
```

Readback during default enforcement temporarily masks channels/disables CSAs.
If an operating configuration unmasks channels, the inherited final restoration
sends ten mask/CSA writes; it does not itself verify those final writes. For an
explicit full desired-state readback gate, after reviewing the selected JSONs
and separately approving any trigger-enabling effects, use the existing option:

```bash
python configure_larpix.py --pacman_config io/pacman_io1.json --no_unmask_last --verbose
python configure_larpix.py --pacman_config io/pacman_io2.json --no_unmask_last --verbose
```

These remain separate per-IOG approvals. Fresh networking defaults should be
masked; confirm their contents before using this option. Capture successful
readback/enforcement, chip counts, paths and process exit codes, not only root
replies or TCP connectivity.

Networking may retry once after failed enforcement. It hard-resets/replays the
failed tile and re-enforces every chip reset on that tile plus remaining work,
using the original endpoint/family. The intended configuration remains in
memory on failure; failed enforcement does not automatically unmask hardware.
The donor's final 24-cycle soft reset remains at IOG scope.

## Stage 4 — approved two-process operation

Only after both independent fresh-start gates pass, obtain approval for both
IOGs/tiles together and repeat power-down/power-up, then:

```bash
bash run/iog/network.sh all
bash run/iog/configure.sh all
```

The two control processes stay homogeneous. Confirm intentional reuse of each
selected default, separate IOG logs and correct aggregate failure status. Failure
isolation is covered with offline injected failures; do not deliberately fault
live hardware without a separate exact procedure and approval. Wrappers remain
specialized to this two-board deployment (`all|1|2`).

## Recovery and remaining hardware uncertainties

Stop the failed command, preserve logs, leave the other board alone. Following
approval for safe power-down of the affected PACMAN, use exactly one of:

```bash
bash run/iog/power_down.sh 1
bash run/iog/power_down.sh 2
```

These disable RX, all ten tile outputs/DACs and MCLK on the selected PACMAN.
Software rollback is selection of preserved, reviewed topology/default files
followed by the same supervised fresh-start sequence. Do not restore a non-null
current-state index as a substitute for hardware initialization. If the command
server is unresponsive, use the lab's approved manual power procedure; do not
assume another software command restored a safe electrical state.

The pinned library's `reset_tiles()` constructs its exclusion mask using only
tiles 1–8. It cannot establish isolation of tiles 9/10 from code inspection. The
current deployment powers only tile7 on IOG1 and tile10 on IOG2; do not extend
these tests to other powered tiles without firmware/library review. No library
register maps, reset protocol or power ordering were changed in this repair.
V3 signal timing, all-chip discovery, fresh reset/replay and final readback still
need the supervised gates above. Mixed raw acquisition, geometry relabeling and
new detector-scale orchestration remain later milestones.
