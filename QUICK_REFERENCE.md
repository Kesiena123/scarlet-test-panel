# Block Position Monitor — Quick Reference

Firmware **4.1.0** (build 2026-08-29) · protocol **4.1**. Everything here is
the operator-facing cheat sheet; the full byte-level protocol is in
`encoder/encoder/PROTOCOL.md`.

## The model

A piecewise-linear calibration table of up to 4 anchors `(encoderCount,
positionFt)` — one per layer. Between two anchors:

```
position = P1 + (count - C1) / (C2 - C1) * (P2 - P1)
Reported position = interpolated position + witsCorrectionFt
```

Operator-defined, persisted on the firmware:

| Setting | Meaning | Default | Range |
|---------|---------|---------|-------|
| Calibration anchors 1–4 | `(calCounter, calPosition)` pairs, one per layer | all 0 (not configured) | position ft -100,000 … 1,000,000 |
| WITS correction | `witsCorrectionFt` offset added to reported position | 0.0 | -100,000 … 1,000,000 |
| Encoder polarity | `encoderPolarity` direction sign applied to every edge | +1 | +1 / -1 |

No PPR / decoding mode / gear ratio / wheel diameter, and no single
`countsPerFoot` anywhere. Position is **never extrapolated** outside the
anchors (shows `OUT_OF_RANGE`).

Calibration points are validated on entry: duplicate pulses/positions,
zero-length intervals, and counter↔position direction conflicts are rejected
(you'll see the `ec` code); a point must not contradict the other anchors.

## Dashboard

- **Block tab** — big live `blockPositionFt`, `direction` (UP / DOWN /
  ON BOTTOM / STOPPED), `velocityFtMin`, `currentLayer`, `currentTicks`, and a
  **CALIBRATION table** (Level | Encoder Counts | Position (ft) | Counts/ft)
  with selectable rows. The table **is** the calibration input: the
  **ENCODER COUNTS** (encoder counter anchor) and **POSITION (FT)** (position
  anchor) cells are editable — type the physical values directly in a row,
  then **SET LAYER** pushes them to the device (counts→counter, position→ft).
  **COUNTS/FT** is auto-computed per row. RUN / CALIBRATE toggle,
  **Set Layer**, **Set Block Height**, **Set WITS**, **Reset**, **Load**,
  **Save**, **Delete Level**, **Set Polarity**.
- **Settings** — operator edits go through the two-stage protocol; you see
  **PENDING → SAVED** only after the device echoes and you verify.
- **RESET COUNTER** — device replies `reset_ack` with `currentTicks:0`; the
  banner shows **RESET SUCCESSFUL** only then. The counter zeroes and the
  position is preserved via a runtime reference (the calibration table is
  never re-based, modified or saved), never deleted.
- **DELETE LEVEL** — removes the selected calibration row only; the live
  counter is never touched (separate from RESET COUNTER).
- **SET POLARITY** — toggles the encoder direction polarity `+1`/`-1` if the
  cabling reads backwards.
- **RESTORE DEFAULTS** — clears the calibration table + WITS and resets
  polarity to `+1` (keeps the counter). Requires CONFIRM.
- **About tab** — firmware version/build, protocol version, uptime, boot
  reason, last boot error.

## Serial protocol (5-second memory)

- Single-line JSON + Fletcher-16 `,"crc":N}` tail. CRC covers chars between
  the opening `{` and `,"crc":`.
- Snapshot on connect: `{"cmd":"status"}` → `status` message.
- Change a setting: send the staged command, then
  `{"cmd":"confirm"}` → `ack state:"done"` (echoes the ACTIVE config to
  verify). `awaiting_confirm` = staged-not-applied; `rejected`/`error` =
  nothing changed.
- Set a calibration point: `{"cmd":"set_calibration_point","layer":1,
  "value":31.58,"counter":4437}` (counter optional → uses live counter).
- WITS: `{"cmd":"set_wits_correction","value":0.5}`.
- Polarity: `{"cmd":"set_encoder_polarity","value":-1}`.
- Delete one level: `{"cmd":"clear_calibration_point","layer":3}` (never
  touches the live counter).
- Persist now: `{"cmd":"save_calibration"}` / `{"cmd":"load_calibration"}`.
- Zero the counter: `{"cmd":"reset_counter"}` → `reset_ack` with `currentTicks:0`.
- Error codes: 0 OK · 1 UNKNOWN cmd · 2 PARAM · 3 VALUE · 4 OUT_OF_RANGE ·
  7 NO_PENDING · 8 OUT_OF_MEMORY · 9–14 calibration validation (9 dup pulses ·
  10 dup position · 11 zero interval · 12 bad order · 13 few points ·
  14 bad polarity).
- `uptime_s` decreasing = device rebooted → re-assert settings.

## Operator workflow at a new installation

1. Put the dashboard in **CALIBRATE** mode; jog the block to the lowest
   physical position and read the tape in feet.
2. **Set Layer / Block Height = 31.58** — binds Layer 1 to the live counter.
3. Repeat at higher positions for layers 2, 3, 4 (usually 2 or 3 suffice).
4. Optionally enter **Set WITS** if the site readout needs an offset, or
   **Set Polarity** if the counter counts backwards.
5. Confirm each change; watch for the green **SAVED**. If a point is rejected,
   fix the duplicate/out-of-order value and re-submit.
6. If needed, **Delete Level** to drop a single bad row, **Restore Defaults**
   to start over (keeps the counter), and **Reset Counter** at a fresh jog
   position.

## Pitfalls

- 31.58 ft after a reset is **correct**: no fresh `report` arrives until the
  next 100 ms data cycle; the displayed value is the last verified one.
- The position display is only trusted **between** the calibrated anchors;
  below the lowest or above the highest anchor it shows `OUT_OF_RANGE` and
  never extrapolates.
- CONFIRM must be sent for every staged setting; otherwise the staged value
  is discarded (nothing applies, `ec:7`). `save_calibration` / `load_calibration`
  / `reset_counter` apply immediately (no CONFIRM).
- Never paste a dashboard `report`/`status` line back verbatim and expect it
  to round-trip — `report`/`status` are read-only; commands use the
  `{"cmd":...}` verbs.
