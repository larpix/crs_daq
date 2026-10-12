# CRS donor restoration — 2026-10-11

## Baseline and preservation

No hardware operations are authorized or performed by this repair. Source and
runtime baseline: `/tmp/cern-preservation-l9i87a7m/cern-state.tar.gz`, with
`manifest.json` SHA-256 hashes, tracked/staged patches, donor patches/status, and
`function-comparison.txt`. The snapshot contains 327 files, including selected
ASIC directories, generated topologies, hidden indexes, logs and untracked work.
The audit bundle's eight supplied checksums verified; its unpacked evidence is
at `/tmp/cern-donor-audit-pxp823bk`. These temporary paths should be archived by
the operator if long-term retention is needed.

| Source | Exact commit | Reconciliation |
|---|---|---|
| CERN | `c10fb212b10215afc369ecf8142d37376d86243f` | Matches audit; branch `cern-coldbox-v2d-v3a-hijinks` |
| FSD donor | `a020244ba774bd9be1bbbd850b423dc320a0d700` | Matches audit; local RUN_CONFIG/connection edits and generated files preserved |
| v3 donor | `4a8b40f1abdcdbf3f66a75b7bb48f3259a3c12b9` | Matches audit; local RUN_CONFIG/connection edits preserved |
| 2×2 ancestor | `3ccef841f1639f7199063530e485ddbbcd4b338c` | Available locally for direct comparisons |
| larpix-control | `5a69050422e82356c8faf9ad0ea3168c322d63e8` | Clean source checkout; Python resolves `/home/bgelli/cern-coldbox/src/larpix-control/larpix` |

CERN tracked local edits are only `.envrc` PIDs and the three `io/pacman*.json`
connections: IOG1 `.104`, IOG2 `.100`. They must remain unstaged and unchanged.
The untracked donor entry point `hydra_v3_original.py`, plot helper, diagnostic
logs, generated configs and smoke JSON are preserved. No donor files are edited.

The default and current indexes both select:

| IOG | Topology | Selected ASIC directory | Observed coverage |
|---|---|---|---|
| 1 | `iog_1-tile_7-hydra-network.json` | `asic_configs/asic_configs-2026_09_10_08_25_14_PDT/iog1` | 160 topology keys, 160 matching `2d` JSONs |
| 2 | `iog_2-tile_10-hydra-network.json` | `asic_configs/iog2` | Topology requests `2-39-1`; only `2-37-1` exists, family `3` |

The network index points to those same topology files. IOG2's mismatch is now
reproduced by filesystem inspection, not inferred from a timeout. Empty new
timestamp directories reflect default reuse; the writer was never removed.
`base/config_loader.py` has donor blob `4657cd72ed3d2ac317a944ef48cfe384c4941ed9`
and will remain byte-identical. LOG5 records v2d discovery with zero outstanding
chips; LOG6 records successful v2d replay. LOG6's three v3 roots precede the
channel-40 exception: full v3 discovery/configuration remains unvalidated.

## Function-level decision ledger (prepared before code changes)

F/V/A below refer to the pinned FSD/v3/ancestor commits above. Comparisons use
direct endpoints and AST function bodies, not merge-base change counts.

| File / function | Donor and actual difference | Decision and evidence | Focused regression |
|---|---|---|---|
| `network_larpix.main` | F/V conditional generation retained; CERN adds correct family/endpoint dispatch but eagerly publishes selections | Keep architecture/writer; validate topology and loaded coverage before IO; report reuse/generation; publish current selections after success; preserve old defaults on failure | Absent/valid/stale/partial/wrong-family defaults; explicit refresh; failure state |
| `configure_larpix.main` | A body retained except family/endpoint plumbing; `/None` join and silent missing-chip removal are inherited | Fix optional leaf-directory argument; retain uniquely resolvable within-tile remapping, reject missing/ambiguous/foreign chips before IO | Leaf/parent paths; remapping; rejected mismatch; normal configure consumption |
| `base/config_loader.*` | All functions and bytes equal V | Preserve entirely; no new configuration schema or registry | Real writer/loader round trip for both classes |
| `run/iog/network.sh` | New two-IOG deployment wrapper creates empty timestamp parent during reuse | Keep entry point, logging and wait/status; expose existing `--update_default`; let Python create a fresh destination only when saving | Reuse creates no unused directory; explicit refresh forwarded; child failure |
| `run/iog/configure.sh`, `power_up.sh` | Deployment-specific `all|1|2` wrappers | Keep documented two-board selection and wait/exit semantics; generic detector expansion deferred | Per-IOG argv and failure isolation |
| `hydra_v3.main` | Replaced V traversal with root-only bootstrap | Restore V roots, initial discovery, waitlist and export with explicit endpoint/family; reject failed live roots/incomplete discovery; preserve diagnostics in baseline archive | All stages reached with mocked IO; skip unavailable 40; failure cannot publish production output |
| `base/network_base_FSD_v3` root/discovery functions | 16 function bodies equal V; replay adds CERN endpoint/infrastructure | Preserve traversal; fix unavailable links throughout export/replay; inspect loaded graph semantics | Full discovery mock; real graph round trips; replay family/endpoint |
| v3 UART dependency | CERN imports old `base/uart_base`, eight functions differ from V; V and F UART helpers differ only in `setup_daughter` | Reuse unchanged FSD shared UART primitives with a V daughter adapter; keep FSD v2d helper untouched; no duplicate operational entry point | V3 daughter setup avoids v2d `ref_current_trim`; direction/configuration tests |
| `pacman_base.convert_io_channel_to_uart`, RX helpers | V returns None for unavailable links; CERN removed compatibility function and raises | Restore donor-facing skip contract, retain strict diagnostic mapper and invalid-range errors | Bulk tile10 masks; unavailable vs invalid input |
| `pacman_base.set_packet_delay` | V third positional argument is all-UART delay; CERN changed it to channel | Restore original signature/meaning; retain clearly named per-channel helper for diagnostics | All 32 registers, positional delay, low-byte preservation |
| `network_larpix.enforce_iterative` | F/V reconstruct affected tiles; CERN simply repeats enforcement | Restore targeted reconstruction with correct family/endpoint; re-enforce all chips affected by a tile reset, including earlier successes | Both families; exhausted retries; tile/IOG isolation; desired configuration preserved |
| `enforce_parallel.enforce_parallel` | Identical to A, including failure path leaving temporary masks in memory | Fix inherited restoration bug with finally; retain strict failure and timing; use semantic register names for final mask writes | Failure/exception restore masks/CSA; no false success; both family register maps |
| `configure_pacman.main` | CERN rewrote electrical sequence and omitted F/V current-state invalidation | Restore invalidation without deleting defaults; retain current electrical sequence, voltage DACs, reset/settle/clock settings pending hardware review | Mock operation ordering; success/failure invalidation; other IOG untouched |
| `base/network_base_FSD` discovery + `uart_base_FSD.*` | 15 network bodies and all 9 UART bodies match F; imports isolated | Preserve known-working v2d algorithm and IDs 21/61/101 | Existing tests plus saved 160-chip fixture |
| FSD `write_network_to_file`, `_serialized_nodes`; `network_config.UART_MAPS` | UART-labelled serializer and atomic validation differ from F | Do not revert arrays on appearance; compare loaded directed graphs and replay link settings; retain compatible working output | Donor slot encoding vs CERN encoding, real fixture and link settings |
| `hydra_v2d`, `tile_layout` | Absolute tile7 root table / optional physical-label feature | Defer geometry and generalization; preserve root101; no operational relabeling | Working tile7 baseline retained |
| `utility_base`, `asic_base` | Mixed inherited/configuration/acquisition differences; UART RX and broadcast family adaptations | Keep strict reconciliation and necessary family/RX plumbing; no bulk donor copy or acquisition changes | Dependency-level mocked calls |
| `CERN_HARDWARE_VALIDATION.md` | Promotes root-only and destructive regeneration steps | Replace with exact supervised gates, preserved outputs and recovery; raw acquisition deferred | Review against restored CLI |

No audit finding is disproved by the local state. Two boundaries are confirmed:
missing configuration saving is not a regression, and optional-path/silent-drop
behavior plus mask restoration defects predate CERN. The additional v3 UART
dependency mismatch must be repaired to make restored traversal meaningful.

## Validation and final changes

Configuration handoff: 30 tests passed, zero skipped. Commit `3d2fda2` adds
preflight coverage/family checks, explicit generation/reuse, fresh output
reservation, success-only current selections, and wrapper refresh forwarding.
The writer remains unchanged. A fresh reserved directory may remain after a
hardware failure; the log explains the attempted generation and no failed
directory becomes the selected default.

Discovery/API compatibility: 38 tests passed, zero skipped. Full v3 traversal,
waitlist, export and replay execute against mocked transport/readback, reaching
160 chips with matching upstream/downstream/POSI settings. The v3 daughter
adapter uses the eight FSD UART primitives identical to V, without changing the
v2d module. Plotting is removed from the hardware entry point's import chain;
the operator's untracked plot helper remains untouched. Root-only diagnostics
remain in the baseline archive/Git history and external scratch area, not the
normal discovery command. V3 discovery requires a fresh output name and leaves
saved defaults intact while invalidating current state before reset.

Real saved v2d fixtures have identical loaded directed UART-labelled graphs on
live channels despite different JSON maps. Their replay settings are identical
and CERN export/reload preserves that graph. The donor fixture additionally
contains an unused external node on dead channel28. Fixture hashes/provenance
are in `tests/fixtures/README.md`. An inherited `configure_asic_network_links`
side effect adds auxiliary DS/POSI root-neighbor edges to the source controller
during export; those are not serialized. Tests compare the original graph with
the reloaded output, rather than mistaking that mutated intermediate for the
saved topology. No v2d serializer/map change is justified or made.

Discovery/API changes are committed as `5e6cc31`. The third change group restores
targeted retry reconstruction and current-state invalidation. It preserves the
electrical power sequence and original one-retry policy; the desired controller
survives reconstruction and temporary masks/CSA settings are restored on failed
returns and exceptions. A tile reset requires re-enforcement of earlier
successful chips on that tile, not just the originally failed chip. Remaining
unattempted work is also retained. Unknown/foreign retry keys fail explicitly.
Even `ignore_diff=True` now collects mismatches without turning a later
successful batch into success for the whole operation. Replay preflight checks
also run at the underlying family-specific entry points before IO construction.

Power-up invalidates current ASIC/network selections before its first register
operation. This restores the donors' success-state contract and also leaves
state invalid if a power operation fails partway through. Saved defaults and
the other IOG's selections are retained. Mock assertions check the unchanged
initial power/DAC order. Discovery likewise invalidates current v3 selections
before reset. `runenv` and the three existing index schemas are unchanged.

Final offline result: **51 passed, 0 failed, 0 errors, 0 skipped**, including the
18 existing tests and 33 added tests. Command: `python -B tests/run_offline.py`.
The final log is `/tmp/cern-preservation-l9i87a7m/tests-final.log`; earlier
phase logs are alongside it. Twelve changed/test Python files passed
compile-only checks, all four IOG wrappers passed `bash -n`, and
`git diff --check` passed. No real PACMAN constructor, socket connection,
register access, reset, power operation or acquisition occurred. The temporary
wrapper copies invoked a PATH-injected stub instead of production Python.

The complete baseline AST comparison is in `DONOR_FUNCTION_COMPARISON.txt`.
Unreviewed acquisition, crawler, threshold and geometry differences remain
outside this control-path repair. No register maps, library code, physical
root IDs, raw-data format, donor files or runtime selections were changed.
Final SHA-256 comparison confirms original endpoints, `.envrc`, hidden indexes,
ASIC directories, generated topologies and logs are unchanged. Both donors'
tracked patches/status still match the baseline; larpix-control remains clean.

Remaining gates and exact approved-operation proposals are in
`../CERN_HARDWARE_VALIDATION.md`: independently reproduce v2d discovery; complete
v3 discovery; review fresh outputs; then for each IOG perform controlled
power-down/up, normal saved-network replay, configure and full desired-state
readback with no scratch bootstrap. Finally validate the two homogeneous
processes together. These hardware operations remain unperformed and require
explicit approval.

Two limits must be carried into that review: the pinned library's
`reset_tiles()` masks only tiles 1–8 (physical tile9/10 reset isolation is not
established), and inherited final unmask writes are not themselves read back.
The existing `--no_unmask_last` option is documented for a separately approved
full desired-state readback after inspecting the selected trigger/mask settings.
V3 discovery/replay analog/timing differences remain donor settings awaiting
bench validation. Mixed raw acquisition stays deferred.
