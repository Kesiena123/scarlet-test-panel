# Final Test Report — Block Position Monitor

Device: Arduino Mega 2560 + quadrature encoder · Firmware **4.1.0**
(build 2026-08-29) · Protocol **4.1** · Spec of record: `encoder/promt1.txt`
and `encoder/promt2.txt` (acceptance tables with §30.7 marking rules).

Scope: end-to-end verification of the multi-point piecewise-linear calibration
model, the complete removal of the single-`countsPerFoot` (v3) and older
encoder-calibration systems, and the promt2 additions (configurable encoder
polarity, single-level delete, calibration validation).

---

## 1. Summary

| Area | Result |
|------|--------|
| Model correctness (multi-point interpolation, no extrapolation) | **PASS** |
| Removed-parameter audit (grep for old systems) | **PASS** |
| Dashboard ↔ firmware protocol conformance (v4.1 JSON+Fletcher-16) | **PASS** |
| promt2 features (polarity, delete-level, calibration validation) | **PASS** |
| Headless dashboard integration smoke (`SMOKE_OK`) | **PASS** |
| Python type-check / byte-compile of dashboard | **PASS** |
| Firmware cross-compiled for Arduino Mega 2560 (`arduino:avr:mega`) | **PASS** — 21,304 B flash (8%), 3,434 B RAM (41%) |
| Hardware/lab rows (real encoder, button, EEPROM on Mega) | **NOT VERIFIED** (pending lab/field run of `ACCEPTANCE_TEST.py`) |

## 2. What was verified and how

### 2.1 Model & removal audit — PASS
- Exactly one position model in firmware and dashboard: piecewise-linear
  interpolation between up to 4 anchors
  `position = P1 + (count - C1)/(C2 - C1)*(P2 - P1)` (per-interval
  `countsPerFootN = (C2-C1)/(P2-P1)`), reported position = interpolated +
  `witsCorrectionFt`. Out-of-span is flagged `OUT_OF_RANGE`, never
  extrapolated.
- Project-wide grep found **no** `countsPerFoot` (single), `initialTapeReading`,
  `referenceTicks`, `set_counts_per_foot`, `set_layer_ref`, and none of
  `ENCODER_PPR`, `DECODING_MODE`, `GEAR_RATIO`, `WHEEL_DIAMETER`,
  `PULSES_PER_REV`, `PULSES_PER_FOOT`, `drum_diameter`, `feet_per_rev`,
  legacy `encoder_ft`/gauge attrs, legacy `SETCAL`/`SETLIMIT` verbs.
  (0 matches.)

### 2.2 Firmware (grey-box source review) — PASS by inspection
- 4×4 quadrature transition table `s_translate[4][4]` gives exactly `±1` for the
  8 legal single-line transitions, `0` on diagonals, and counts every illegal
  transition (`g_illegal_count`). The ISR tracks the genuine previous state
  (`s_last_state`, `noInterrupts()`-guarded at boot from the physical lines) so
  no legitimate edge is ever skipped or double-counted.
- Counter reads/writes atomic (`noInterrupts/interrupts`); ISR never blocked by
  serial/EEPROM work; WDT 2 s clean; outbound JSON uses fixed buffers.
- EEPROM: 2 blocks (calibration + counter) each magic + Fletcher-16 guarded with
  fallback-to-defaults on corruption; calibration auto-persists on every change;
  map documented in `CALIBRATION_DEFAULTS.md`.
- Reset: button D4 active-low, 50 ms debounce + hold-tail handling, zeros the
  counter and **preserves position via a runtime reference** — it never
  re-bases, modifies or saves the calibration; `reset_ack` carries
  `currentTicks:0`, preserved `blockPositionFt` and `source`.
- Legacy residue fully removed: the v3 `countsPerFoot` model, the ASCII
  `SETCAL`/`SETLIMIT`/`ZERO` sanitizer, and the PPR `FieldCalibration` module
  are all gone — new legacy-token grep: 0.

### 2.3 Dashboard integration (headless Qt) — PASS
`smoke_dashboard.py` drives the real serial thread with a synthetic device
(real Fletcher-16 frames and the 4-anchor calibration table) and verifies:
- `status` snapshot confirms the device-confirmed calibration model;
- `calibrated_position()` mirror equals the firmware interpolation at anchors
  and mid-interval, and returns `None` (never extrapolates) out of range;
- live reports update `currentTicks`/`blockPositionFt` with clean CRC handling;
- `reset_ack` (currentTicks 0) → `consume_reset_state` returns
  `('done', True, 'RESET SUCCESSFUL…')`; banner correct, including the guard
  where `currentTicks:0` is never mis-read as missing;
- two-stage `set_calibration_point` → `awaiting_confirm` → confirm → `done`
  with device echo verified → `confirmed=True`;
- `set_wits_correction` rejected-ack path keeps the old active value;
- immediate `save_calibration` / `load_calibration` (ec == 0) succeed;
- Fabricated-bad-CRC frames are counted, never displayed.

Output: **SMOKE_OK**.

### 2.4 Protocol conformance — PASS
- Fletcher-16 recomputes correctly for every firmware line format (CRC covers
  `line[1:idx]`, i.e. `{` … before `,"crc":`).
- Command verb/error-code table matches `config.h` (`CmdError`); unknown
  verbs → `CMD_ERR_UNKNOWN`; range checks match the calibration model limits.

### 2.5 promt2 feature verification — PASS
- **Configurable encoder polarity.** `set_encoder_polarity` (two-stage,
  `+1`/`-1`) is applied as a sign on every quadrature edge, persisted to EEPROM
  byte 39 and restored/cleared with the block; bad values rejected `ec:14`.
  Exercised in `smoke_dashboard.py` section 11.
- **Single-level delete.** `clear_calibration_point` (DELETE LEVEL) removes one
  calibration row without touching the live counter, fully separate from
  `reset_counter`; dashboard DELETE LEVEL + handler wired; exercised in
  `smoke_dashboard.py` sections 12–13 (incl. the rejected path).
- **Calibration validation.** `encoder_validate_calibration_point` rejects
  duplicate pulses/positions, zero-length intervals, and counter↔position
  order conflicts with stable codes `ec:9`/`ec:10`/`ec:11`/`ec:12`
  (`CAL_DUP_PULSES`, `CAL_DUP_POSITION`, `CAL_ZERO_INTERVAL`, `CAL_BAD_ORDER`);
  `ec:13` few-points and `ec:14` bad-polarity complete the set.

## 3. Acceptance trace — status

`encoder/ACCEPTANCE_TEST.py` encodes every acceptance row (26 rows, incl. the
two hardware rows) and marks each per §30.7 (**PASS / FAIL / NOT VERIFIED**).
Final run on this record:

**TOTAL: 26 · PASS: 24 · FAIL: 0 · NOT VERIFIED: 2** (exit code 0)

- 24 software/interface rows PASS: quadrature ±1 decoding + direction (incl.
  ON BOTTOM), illegal-transition counting, velocity EMA, atomicity, JSON
  protocol + Fletcher-16, command/error table, two-stage commit + verify
  (dashboard ↔ firmware), multi-point calibration / WITS persistence, reset
  runtime-reference semantics (position preserved, calibration untouched),
  legacy-removal audit, Mega/AVR portability (`IRAM_ATTR`
  no-op), plus promt2: configurable encoder polarity, calibration validation
  (duplicates/intervals/order), and single-level delete.
- The 2 hardware rows stay **NOT VERIFIED** until a live-device run (verify
  with `python encoder\ACCEPTANCE_TEST.py --interactive` on the bench):
  - **Physical reset works** (button D4 → counter 0, ≤ 1 ack per press).
  - **Power-cycle configuration retention** (EPROM survives brown-out).

Never claim PASS for a row that was not actually exercised (§30.7).

## 4. Notes & known items

- Build command (verified on 2026-08-29, `arduino-cli`, core `arduino:avr`
  1.8.8):
  `arduino-cli compile --fqbn arduino:avr:mega encoder/encoder`
  → **PASS**. Result: 21,304 B flash (8% of 253,952), 3,434 B RAM (41% of
  8,192); uses the bundled `EEPROM` 2.0 library. `IRAM_ATTR` is a no-op macro
  for AVR in `config.h` (portability).
- A Windows-console mojibake (`RESET SUCCESSFUL �`) is only the terminal
  codepage; the string in `main.py` is valid UTF-8 with an em-dash — not a
  defect.
- Real-time/lab measurement of the 100 ms `report` period and the 2 s watchdog
  must be confirmed on hardware — NOT VERIFIED.

## 5. Sign-off

Final verdict on this record. Execute `python encoder\ACCEPTANCE_TEST.py`
against a live device to promote the NOT VERIFIED rows.

Date: 2026-08-29 · Build under test: 4.1.0/2026-08-29.
