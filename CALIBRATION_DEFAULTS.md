# Calibration & Defaults (Block Position Monitor)

There is **no hardware encoder calibration** in this product — no
`ENCODER_PPR`, no `DECODING_MODE`, no `GEAR_RATIO`, no `WHEEL_DIAMETER_INCH`,
no single `countsPerFoot`, no trips to a test bench with pulses-per-revolution
math. The calibration is a **multi-point tape measurement** per installation
site (up to 4 anchor points, one per layer), entered once on the dashboard or
over the serial protocol.

## Operator-defined parameters

| Parameter | Field in JSON/UI | Default | Accepted range | Meaning |
|-----------|------------------|---------|----------------|---------|
| Calibration anchors | `calPosition1..4` / `calCounter1..4` | none (all 0 = not configured) | position ft `[-100,000, 1,000,000]` | up to 4 `(encoderCount, positionFt)` anchor pairs, one per layer |
| Per-interval counts/ft | `countsPerFoot1..3` | `0.0` | derived | `(C2-C1)/(P2-P1)` between consecutive anchors (display only) |
| WITS correction | `witsCorrectionFt` | `0.0` ft | position range | operator offset **added** to the reported position (velocity unaffected) |
| Encoder polarity | `encoderPolarity` | `+1` | `+1` / `-1` | direction sign scalar applied to every quadrature edge (promt2.txt) |

`MIN_CAL_POINTS = 2` anchors are required before interpolation is trusted;
fewer anchors ⇒ `NO_CALIBRATION`. Calibration points are validated on entry —
duplicate pulses/positions, zero-length intervals, and counter↔position order
conflicts are rejected. A position of `0.0` is **never** a valid anchor
(`CAL_VAL_ZERO_POSITION` / `CMD_ERR_CAL_ZERO_POSITION`): a `(0, 0.0)` row always
represents "not configured" on both the firmware (`calUsedMask` bit clear) and
the dashboard (`cal_points()` drops `counter==0 AND feet==0.0`), so the two
endpoints can never disagree. The live-counter at the bottom may legitimately
read `0` with a **non-zero** position anchor.

Defaults exist only so the device is safe on first power-up. The calibration
table starts empty (all anchors unused), WITS offset `0.0` — see
`encoder/encoder/config.h` (`DEFAULT_WITS_CORRECTION`, empty table) and the
mirrored dashboard fallbacks in `scarlet_test_panel/config.py`
(`DEVICE_DEFAULTS`). The authoritative values always come from the firmware
(STATUS / verified `done` acks).

## Field calibration procedure (operator)

1. Put the dashboard in **CALIBRATE** mode.
2. Jog the block to the lowest physical position; read the tape in feet.
3. **Set Layer / Block Height = 31.58** — binds Layer 1 to the **live counter**
   and stores `(calCounter1, 31.58)`.
4. Repeat at higher positions for layers 2..4 (each `set_calibration_point`
   may supply a specific `counter`, or fall back to the live counter).
5. Confirm each change; wait for **SAVED** (device echo verified). Each set
   point **auto-persists** to EEPROM.
6. Optionally enter a **WITS correction** (operator offset) if a site readout
   must be shifted, or set the **encoder polarity** to `-1` if the counter
   counts backwards.
7. If a single row is wrong, **Delete Level** to drop it (the live counter is
   untouched); or **Restore Defaults** to clear everything except the counter.

The reference-image values (31.58 / 63.21 / 94.78 / 111.00 ft) are illustrative
only and are seeded only in DEMO mode — never real project defaults.

## Restoration

| Action | Effect | Persistent? |
|--------|--------|-------------|
| **Reset Counter** | `currentTicks = 0` with the position preserved by a **runtime reference** (tick offset = pre-reset ticks, position offset = 0); the calibration table is **never re-based, modified or saved** | yes (counter block); **calibration is never changed or deleted** |
| **Delete Level** | removes ONE calibration row (`calCounterN`/`calPositionN` → `0`/`0.0`); **live counter untouched** | yes (cal block) |
| **Restore Defaults** | clears the calibration table + WITS and resets polarity to `+1` to defaults | yes (cal block); counter untouched |

## EEPROM layout (firmware, v4.1)

Every block is guarded by a magic marker + Fletcher-16; corrupt blocks fall
back to defaults and are rewritten, never trusted:

| Offset | Bytes | Field |
|--------|-------|-------|
| 0 | 2 | `CAL_MAGIC` (0x5C41) |
| 2 | 4 | `witsCorrectionFt` (float) |
| 6 | 16 | `calCounter[4]` (longs) |
| 22 | 16 | `calPosition[4]` (floats) |
| 38 | 1 | `calUsedMask` (bit i set ⇒ point i configured) |
| 39 | 1 | `encoderPolarity` (int8, +1 / -1) |
| 40 | 2 | Fletcher-16 over bytes 2..39 (`CAL_BLOCK_LEN` = 38) |
| 42 | 2 | `COUNTER_MAGIC` (0x5C4E) |
| 44 | 4 | counter (currentTicks, long) |
| 48 | 2 | Fletcher-16 over bytes 44..47 |

Highest byte used = 49 (`EEPROM_SAFE` = 50), so 8 KB EEPROM has ample headroom.
The calibration block auto-persists on **every** calibration change (set point,
clear, restore defaults, polarity change) so it survives power loss. RESET
COUNTER / RESET FEET never save the calibration block (reset never changes it).
The counter EEPROM copy is a power-up seed/trace only; the live count is always
`g_currentTicks` (increment-only via ISR) and never loses a tick to a persist
cycle.
