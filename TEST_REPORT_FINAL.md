# TEST REPORT — FINAL (executive summary)

Supersedes the legacy layouts: documents the **Block Position Monitor**
redesign to the **multi-point calibration** model (promt1.txt + promt2.txt,
firmware 4.1.0 / protocol 4.1). The old single-`countsPerFoot` model (v3) and
the older encoder-calibration project (PPR/gear/wheel → feet) no longer exist
in code, docs, or tests. v4.1 (promt2.txt) adds configurable encoder polarity,
single-level delete, and calibration validation.

## Verdict

| Group | State |
|-------|-------|
| Multi-point model correctness + removal audit | **PASS** |
| Dashboard ↔ firmware protocol v4.1 (JSON + Fletcher-16) | **PASS** |
| promt2 features (polarity, delete-level, calibration validation) | **PASS** |
| Headless dashboard smoke (reset runtime-reference, two-stage, WITS, save/load, delete-level, polarity, stale/link, verify) | **PASS** (`SMOKE_OK`) |
| Dashboard byte-compile / static sanity | **PASS** |
| Firmware cross-compiled for Mega 2560 (`arduino:avr:mega`) | **PASS** — 21,304 B flash (8%), 3,434 B RAM (41%) |
| Hardware rows (quadrature/reset/EEPROM on live Mega) | **NOT VERIFIED** — run `python encoder\ACCEPTANCE_TEST.py` with the device |

## Evidence highlights

1. Removed-model grep: 0 hits for `countsPerFoot` (single), `initialTapeReading`,
   `referenceTicks`, `set_counts_per_foot`, `set_layer_ref`, `ENCODER_PPR|
   DECODING_MODE|GEAR_RATIO|WHEEL_DIAMETER|PULSES_PER|drum_diameter|
   feet_per_rev|encoder_ft` etc.
2. One model: piecewise-linear interpolation between up to 4 anchors
   `position = P1 + (count - C1)/(C2 - C1)*(P2 - P1)`; reported position =
   interpolated + `witsCorrectionFt`; out-of-span flagged `OUT_OF_RANGE`,
   never extrapolated — firmware `encoder.cpp`, dashboard
   `main.py::calibrated_position`, docs `PROTOCOL.md` in agreement.
3. Bug guard retained from v3: `currentTicks=0` is never misread as “missing”
   (`raw is not None and int(float(raw)) == 0`) — a successful device reset
   shows **RESET SUCCESSFUL** (`main.py::_handle_reset_ack`,
   `services/commands.py::on_reset_ack`).
4. Firmware quadrature fix retained: real `s_last_state` tracker over the
   verified `s_translate[4][4]` matrix, seeded from the physical pins at boot
   (`encoder.cpp`) — no legitimate edge is skipped or invented.
5. CRC/Fletcher-16 harness (incl. hostile “trailing `},”crc`”” frames) matches
   firmware `sender` byte-for-byte; `fletcher16` contract unchanged in
   `services/protocol.py`.
6. Two-stage commit fully exercised with the new verbs: stage →
   `awaiting_confirm` → `confirm` → `done` (echo of `calPositionN`/
   `calCounterN` verified); `set_wits_correction` rejected path keeps the old
   active value; immediate `save_calibration`/`load_calibration` succeed.
   promt2: `set_encoder_polarity` (+1/-1) and `clear_calibration_point`
   (single-level delete, live counter untouched) both two-stage, with the
   rejected path also covered.
7. Legacy residue deleted across v4/v4.1: the v3 `countsPerFoot` single model,
   the ASCII `SETCAL`/`SETLIMIT`/`ZERO` sanitizer, and the PPR
   `FieldCalibration` module. Re-grep: 0 matches. (`CAL_VAL_DUP_PULSES` etc.
   are new promt2 validation identifiers, not legacy math.)
8. Acceptance harness run (this record): **TOTAL 26 · PASS 24 · FAIL 0 ·
   NOT VERIFIED 2** (reset-button press; power-cycle retention) — exit 0.
   Grey-box harness `VERIFICATION_TEST.py`: **40/40 PASS**, exit 0.
9. Firmware cross-compiles for **arduino:avr:mega** (21,304 B flash / 3,434 B
   RAM). `SMOKE_OK` + both harnesses re-run green.

## §30.7 marking applied in all reports

Every acceptance row is marked **PASS / FAIL / NOT VERIFIED**; an unrun
hardware row is NOT VERIFIED, never assumed. The field harness enforces this.

## Final

Firmware 4.1.0 (build 2026-08-29) implements promt1.txt + promt2.txt:
operator-defined multi-point calibration anchors (one per layer), WITS offset,
configurable encoder polarity (+1/-1), single-level delete, calibration
validation, verified two-stage configuration, immediate save/load calibration,
reset that preserves position via a runtime reference and never re-bases,
modifies or erases the calibration, and diagnostics (illegal-transition
counter, ON BOTTOM / OUT_OF_RANGE status, boot reason). Dashboard 4.1 consumes
the v4.1 protocol,
never optimistic, verifying every device echo. Ship the harness with the
device.

Date of record: 2026-08-29.
