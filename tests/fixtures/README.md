# Saved topology provenance

These are whitespace-compacted copies captured read-only on 2026-10-11.

| Fixture | Original file | Original SHA-256 |
|---|---|---|
| `cern_v2d_saved_network.json` | CERN `iog_1-tile_7-hydra-network.json` | `c2f9869443d9ce07ac602e9ab0732461bf78640d9b6ac0b3e05ca6fca2ffa551` |
| `fsd_v2d_saved_network.json` | FSD worktree `iog_1-tile_7-hydra-network.json` | `da049429c7bce62085839a0f3f9a7d2add626bddda3e27ab96f84c706ce89576` |

Both are generated local evidence, not tracked files at the donor pins. Each
contains 160 ASICs on channels 25–27. The FSD copy also has an unused external
node on unavailable channel28. Despite different slot arrays, all three directed
UART-labelled graphs on live channels and the library-generated link settings
are equal. The regression test preserves that distinction.

The v3 test exercises the pinned donor-derived traversal itself with mocked
readback, then exports and replays all 160 chips; no full live v3 saved fixture
was available at the start of this repair.
