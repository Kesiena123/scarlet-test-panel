# Scarlet Test Panel — Block Position Monitor

Production-grade operator display + firmware for a **block position monitor**
(promt1.txt + promt2.txt): an encoder measures block/paper movement; the
operator defines a **multi-point calibration table** per installation site
from tape measurements — no encoder hardware constants exist anywhere in the
system.

```
Between two anchors (C1,P1),(C2,P2) with C1 <= count <= C2:
  blockPosition = P1 + (count - C1) / (C2 - C1) * (P2 - P1)
Reported position = interpolated position + witsCorrectionFt
Every quadrature edge is signed by the configurable direction polarity (+1/-1).
```

---

## What it is

| Component | Location | Language |
|-----------|----------|----------|
| Firmware | `encoder/` (`encoder.ino`, `config.h`, `encoder.h/.cpp`, `eeprom_storage.h/.cpp`, `serial_protocol.h/.cpp`) | C++ (Arduino Mega 2560) |
| Dashboard | `scarlet_test_panel/` (`main.py` + `tabs/`, `services/`) | Python 3 + PyQt5 |
| Tests | `encoder/VERIFICATION_TEST.py`, `encoder/ACCEPTANCE_TEST.py` | Python + PySerial (labs/field) |

The firmware is the **single authoritative** measurement engine; the
dashboard is a **verifying client** that never trusts a value until the
firmware confirms it.

## Key concepts (promt1.txt + promt2.txt)

- **Operator-defined model.** The operator defines up to 4 calibration anchor
  points `(encoderCount, positionFt)` — one per layer — via the dashboard or
  the serial protocol. No PPR, decoding mode, gear ratio, wheel diameter, or a
  single `countsPerFoot` anywhere. The old `countsPerFoot`/`initialTapeReading`/
  `referenceTicks` model and the older `ENCODER_PPR/DECODING_MODE/GEAR_RATIO/
  WHEEL_DIAMETER_INCH/PULSES_PER_REV/PULSES_PER_FOOT` system are **removed**.
- **Piecewise-linear interpolation.** Position is linear between anchors and
  **never extrapolated**: out-of-span counts are flagged `OUT_OF_RANGE`
  (`calInRange:0`).
- **WITS offset.** `witsCorrectionFt` is an operator offset added to the
  reported position; velocity/direction are unaffected.
- **Configurable encoder polarity.** `set_encoder_polarity` (`+1`/`-1`) applies
  a direction-sign scalar to every quadrature edge; persisted to EEPROM as
  `encoderPolarity` (promt2.txt).
- **Single-level delete.** `clear_calibration_point` (DELETE LEVEL) removes one
  calibration row without ever touching the live encoder counter (promt2.txt).
- **Calibration validation.** Points are validated on stage/apply: duplicate
  pulses/positions, zero-length intervals, and counter↔position order conflicts
  are rejected with stable `ec` codes 9–14 (promt2.txt).
- **Layers = anchors.** Each of the 4 table rows maps to a layer; `currentLayer`
  is the highest configured anchor at/below the live position (else layer 1).
  The table is persisted on the firmware.
- **Two-stage commits.** Configuration changes are stage → **CONFIRM** → apply;
  the dashboard marks SAVED only after a numerically verifiable `done` ack.
- **Reset ≠ Restore.** RESET zeroes the counter and preserves the physical
  position via a runtime reference **without touching the calibration table**
  (calibration is **never modified or saved** by reset, and never re-based);
  RESTORE DEFAULTS clears the calibration table + WITS (+ resets
  polarity to `+1`). Operators and the physical reset button both zero *the
  counter*, never the calibration (promt1.txt).

## Repository layout

```
encoder/
  promt1.txt                spec of record — acceptance table (v4 baseline)
  promt2.txt                new spec (polarity, delete level, validation)
  encoder/
    encoder.ino             main loop, serial + debounced reset, WDT
    config.h                constants, EEPROM map, version (4.1.0)
    encoder.h / encoder.cpp  quadrature ISR + calibration measurement model
    eeprom_storage.h/.cpp   magic+Fletcher-guarded EEPROM blocks
    serial_protocol.h/.cpp  inbound parser + outbound JSON builder
    PROTOCOL.md             wire protocol reference (v4.1)
    ZERO_MISSED_COUNTS_COMPLIANCE.md
  VERIFICATION_TEST.py      grey-box source/lint checks (lab)
  ACCEPTANCE_TEST.py        field harness against promt1.txt + promt2.txt
scarlet_test_panel/
  main.py                   app entry, serial thread, message pump
  config.py                 shared constants/colors
  tabs/block_position_tab.py  operator block-position HMI
  tabs/                   about, diagnostics, settings, alarms, calibrate...
  services/protocol.py      Fletcher-16 + message validation
  services/commands.py      two-stage command tracker
  services/settings.py, layers.py, security.py, ...
```

## Wiring (firmware, Arduino Mega 2560)

| Signal            | Mega pin |
|-------------------|----------|
| Encoder A (quadrature) | D2 (INT0) |
| Encoder B (quadrature) | D3 (INT1) |
| Reset button (active-low to GND) | D4 |
| Serial → dashboard | USB (UART0, 9600 8N1) |

Both quadrature inputs are `INPUT_PULLUP`, active-low against the pull-ups
(promt1.txt). Reset is a momentary button to GND, software-debounced
(50 ms).

## Build the firmware (verified 2026-08-29)

```
arduino-cli compile --fqbn arduino:avr:mega encoder/encoder
```

Result: 21,304 B flash (8%), 3,434 B RAM (41%). Uses the bundled `EEPROM`
2.0 library.

## Run the dashboard

```
python -m scarlet_test_panel.main
```

Build/ship via `PyInstaller (one-dir) + Inno Setup` (see
`scarlet_test_panel/scripts/`) for a Windows kiosk app.

## Run the tests

```
python encoder\ACCEPTANCE_TEST.py    # field harness → PASS/FAIL/NOT VERIFIED
python encoder\VERIFICATION_TEST.py  # lab source/lint harness
```

## Versioning

- **Firmware:** `FW_VERSION "4.1.0"`, build `2026-08-29`, protocol `4.1`.
- **Protocol:** v4.1 JSON/Fletcher-16 — see `encoder/encoder/PROTOCOL.md`.
- Legacy v3.0 `countsPerFoot` model (and the older encoder-calibration model)
  is fully removed; no compatibility shims are kept (breaking change,
  intentional).
- v4.1 (promt2.txt) adds configurable encoder polarity, single-level delete,
  and calibration validation on top of the v4.0 multi-point model.

## Status

Complete and reviewed end-to-end (firmware compiled on `arduino:avr:mega`;
dashboard + both test harnesses run clean; headless `smoke_dashboard.py`
prints `SMOKE_OK`). See `FINAL_TEST_REPORT.md` for the device-level
verification record.