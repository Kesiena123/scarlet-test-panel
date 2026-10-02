# Device ↔ Dashboard Serial Protocol (Reference)

Firmware: `encoder` (Arduino Mega 2560) · Protocol version **4.2**

This document is the single source of truth for communicating with the
**Block Position Monitor** firmware. A dashboard (or any integrator) can
implement a compatible client from this document alone, without reading the
firmware source. The reference implementation that consumes this protocol is
`scarlet_test_panel/services/commands.py` + `scarlet_test_panel/main.py`.

---

## 1. Transport

- **Physical link:** UART over USB. **Baud:** `115200`, 8N1.
- **Framing:** each JSON object is a **single line** terminated by `\n`
  (a `\r` may also appear and is treated as a terminator).
- **Line limits:** inbound lines up to `SERIAL_LINE_MAX` (220 bytes) are
  processed; anything longer is truncated.
- **Integrity:** every line ends with a Fletcher-16 checksum field
  `,"crc":NNNN}`. The checksum covers the **entire JSON body** between the
  opening `{` and the `,"crc"` marker — i.e. `line[1:idx]` where `idx` points
  at `,"crc":`. This matches the C++ outbound builder exactly: everything
  written after the leading `{` (and before the `,"crc"` tail) is fed to the
  checksum.

### Fletcher-16 (modulo-255 variant)

```
sum1 = 0; sum2 = 0
for each byte b in body:
    sum1 = (sum1 + b) % 255
    sum2 = (sum2 + sum1) % 255
crc = (sum2 << 8) | sum1
```

`crc` is sent as an unsigned 16-bit decimal integer. The same algorithm is
implemented in `scarlet_test_panel/services/protocol.py` (`fletcher16`) and
used for the EEPROM blocks in `eeprom_storage.cpp`.

> **Critical for integrators:** the `"crc"` field must be the **last member
> inside** the JSON object (`{...always...,"crc":N}`). A trailing
> `},"crc":N}` (outside the object) is invalid JSON and is rejected as a
> checksum failure.

---

## 2. Message framework

### 2.1 Message types (`type` field)

| `type`      | Direction        | Meaning                                        |
|-------------|------------------|------------------------------------------------|
| `status`    | firmware → host  | Full active measurement-model snapshot         |
| `report`    | firmware → host  | Periodic live reading (every 100 ms)           |
| `ack`       | firmware → host  | Reply to a staged/immediate command (awaiting/done/rejected/error) |
| `reset_ack` | firmware → host  | Confirmation that the counter is now 0         |
| `log`       | firmware → host  | Device-side audit event                        |

### 2.2 Correlation — `req_id`

Every command the host sends **should** carry `"req_id":N`. The firmware
echoes the same `req_id` in the matching `ack` (and a `reset_ack` is matched
through the tracked request). If omitted, `req_id` defaults to `-1`
(no correlation heading is emitted).

### 2.3 Device clock — `uptime_s`

The Arduino has **no RTC**. Every outbound message carries `uptime_s`
(seconds since boot). If the value **decreases** between two messages, the
device rebooted (watchdog or power cycle); the host must re-assert the active
configuration after a reboot. `sequence` is a monotonically increasing
outbound message counter (audit aid only).

---

## 3. Commands (host → firmware)

The firmware parses a tolerant single-line JSON object. Whitespace between
tokens is accepted. Unknown `cmd` verbs → `ack state:"error"` with `ec:1`.

### 3.1 `set_calibration_point` — **two-stage** (stage then CONFIRM)

```
{"cmd":"set_calibration_point","layer":1,"value":31.58,"counter":4437,"req_id":1}
{"cmd":"confirm","req_id":1}
```

- `layer` is **1-based** and must be `1..MAX_CAL_POINTS` (4) else `ec:4`.
- `value` = the physical block position (ft) at that layer's calibration
  anchor. Must be numeric (`ec:3`) and within the position range else `ec:4`.
- `counter` = the encoder counter reading registered to that anchor.
  **Optional**: if omitted, the firmware binds the point to its **live**
  counter — i.e. the operator has positioned the block at the physical point
  and "pins" it to wherever the counter reads right now.
- A row whose `value` (and `counter`) is `0` is "not configured".

Stage → `ack state:"awaiting_confirm"` (`ec:0`). The host then sends
`{"cmd":"confirm","req_id":N}` to apply. Applying **auto-persists the whole
calibration table to EEPROM** and returns `ack state:"done"` echoing the
**ACTIVE config** (the measurement summary incl. `calPositionN`/`calCounterN`)
so the host can numerically verify the value actually stored and in use.

### 3.2 `set_wits_correction` — **two-stage**

```
{"cmd":"set_wits_correction","value":0.5,"req_id":2}
{"cmd":"confirm","req_id":2}
```

- `value` = operator WITS offset in feet, added to the reported
  `blockPositionFt` for display only (velocity/direction are unaffected).

### 3.3 `restore_defaults` — **two-stage**

```
{"cmd":"restore_defaults","req_id":6}
{"cmd":"confirm","req_id":6}
```

Applies the compile-time defaults: **clears the calibration table** (all four
anchors → unused), **zeroes the WITS offset**, **resets the encoder polarity
to `+1`**, and persists them. **The live encoder counter is deliberately
preserved** — zeroing the count is a separate `reset_counter` operation. The
`done` ack echoes the new ACTIVE calibration.

### 3.3a `clear_calibration_point` — **two-stage** (DELETE LEVEL, promt2)

```
{"cmd":"clear_calibration_point","layer":2,"req_id":9}
{"cmd":"confirm","req_id":9}
```

Deletes **one** calibration level (`layer` 1..4, else `ec:4`). The row's
`calCounterN`/`calPositionN` revert to `0`/`0.0` (unconfigured) and the whole
table auto-persists. This is **fully separate from `reset_counter`**: the live
encoder counter is **never** disturbed by deleting a level. Validation errors
(see §6) are reported as `rejected` with the matching `ec`.

### 3.3b `set_encoder_polarity` — **two-stage** (promt2)

```
{"cmd":"set_encoder_polarity","value":-1,"req_id":10}
{"cmd":"confirm","req_id":10}
```

Configures the encoder **direction polarity** scalar applied to every
quadrature edge: `value` is `+1` or `-1` else `ec:4` (or the dedicated
`ec:14` for a non-polarity value). `-1` inverts the sign so the counter moves
the other way for a given shaft direction. The value is persisted to EEPROM and
echoed as `encoderPolarity` in the measurement summary.


### 3.4 `reset_counter` — **immediate** (no CONFIRM step)

```
{"cmd":"reset_counter","req_id":5}
```

Zeroes `currentTicks` to `0` and preserves the physical position via a
**runtime reference** (the same mechanism as RESET FEET, but with the position
offset left at zero so the reading is unchanged rather than reading 0.00).
It does **not** re-base, save, clear, delete or overwrite the calibration
table — every stored `calCounterN` / `calPositionN` and the saved EEPROM
calibration stay **byte-for-byte identical** (only `restore_defaults` erases
calibration). The device replies with a dedicated `reset_ack` carrying
`"currentTicks":0` and the preserved `blockPositionFt`; the host must show
**RESET SUCCESSFUL** only after it validates that exact message.

### 3.4a `reset_feet` — **immediate** (runtime position only)

```
{"cmd":"reset_feet","req_id":9}
```

RESET FEET is a **different operation** from `reset_counter`. It zeroes the
live encoder counter and re-establishes a **temporary runtime zero reference**:
`currentTicks = 0`, `blockPositionFt = 0.00`, `velocityFtMin = 0.00`,
`direction = STOPPED`. It **never** re-bases, saves, clears, deletes or
overwrites the calibration table — the `calCounterN` / `calPositionN` anchors,
saved pulses/feet and EEPROM calibration are completely unchanged. The device
replies with a dedicated `reset_feet_ack` carrying `"currentTicks":0` and
`"blockPositionFt":0.0`; the host must show **RESET FEET SUCCESSFUL** only
after it validates that exact confirmation. To persist a restored position you
would later re-establish calibration separately.

### 3.5 `save_calibration` / `load_calibration` — **immediate**

```
{"cmd":"save_calibration","req_id":7}
{"cmd":"load_calibration","req_id":8}
```

- `save_calibration` — force the live calibration + WITS offset to EEPROM.
  `done` ack with `ec:0` confirms persistence.
- `load_calibration` — reload the stored calibration from EEPROM into RAM.
  `done` with `ec:0` on success; `error` with `ec:7` if no valid stored
  calibration exists.

### 3.6 `status` — read-only

```
{"cmd":"status"}
```

Returns the full `status` snapshot. Sent by the host on every connect/reconnect
so it displays (and verifies against) what the device is **actually using** —
never a cached value.

### 3.7 `confirm`

```
{"cmd":"confirm","req_id":<original>}
```

Applies whichever action is currently staged. If nothing is staged →
`ack state:"error"`, `ec:7` (`CMD_ERR_NO_PENDING`).

### 3.8 `sensor_set_param` — **two-stage** (16-channel analog bank)

```
{"cmd":"sensor_set_param","sensor":3,"param":"value_high","value":75.0,"req_id":11}
{"cmd":"confirm","req_id":11}
```

Updates **one** calibration/configuration parameter of analog **Sensor 1..15**
(`sensor` must be `1..15`; Sensor 0 is the physical hookload chain and is
owned by the hookload model — it is **never** writable through this command).
`param` is one of:

| `param`          | Meaning                                        | Range |
|------------------|------------------------------------------------|-------|
| `voltage_low`    | two-point low calibration volts               | 0..6 V |
| `voltage_high`   | two-point high calibration volts              | 0..6 V |
| `value_low`      | engineering value @ low point                 | ±1e6 |
| `value_high`     | engineering value @ high point                | ±1e6 |
| `min_eng`        | below this engineering value → LOW/FAULT      | ±1e6 |
| `max_eng`        | above this engineering value → HIGH/FAULT     | ±1e6 |
| `gain`           | voltage scale-correction (corrected = raw·gain + offset) | 0.5..1.5 |
| `offset`         | voltage offset (V)                            | ±0.5 V |
| `filter`         | EMA window (1 = no filtering)                 | 1..64 |

Validation happens **before** staging: unknown `param` → `ec:3`; value outside
the accepted range → `ec:4`. Confirm applies the change, **auto-persists the
whole 16-channel bank block to EEPROM**, and returns `ack state:"done"`.
Sensor 0 rejects with `ec:4` (`sensor` must be `1..15`).

### 3.9 `sensor_reset` — **immediate**

```
{"cmd":"sensor_reset","sensor":4,"req_id":12}
```

Resets **one** channel's **live** fault/status state (`0..15`) — NO_DATA →
re-acquired. Calibration is **never** modified. `done` ack.

### 3.10 `sensor_factory_reset` — **immediate**

```
{"cmd":"sensor_factory_reset","req_id":13}
```

Restores **Sensor 1..15** to the dashboard-factory two-point calibration
defaults and persists them. **Sensor 0 is untouched** (owned by hookload). A
`log` event `"ANALOG: sensor bank returned to factory calibration by
operator"` is emitted.

---

## 4. Message schemas (firmware → host)

All schemas below are the **authoritative** byte-for-byte format emitted by
`serial_protocol.cpp` (quoting, precision, and key order). Common wrapper:
`{<fields>,"crc":N}`.

### 4.1 `status`

```
{
  "type":"status",
  "fw_version":"4.1.0",
  "build":"2026-08-29",
  "protocol_version":"4.1",
  "boot_reason":"POWER_ON|WATCHDOG",
  "boot_error":"",                     // e.g. "calibration block defaulted"
  "req_id":n,                          // present only if requested
  ... measurement summary ...,
  "uptime_s":123,
  "sequence":12,
  "crc":N
}
```

### 4.2 `report` — periodic live reading (every `DATA_PERIOD_MS` = 100 ms)

```
{
  "type":"report",
  "currentTicks":4437,
  "blockPositionFt":31.58,
  "velocityFtMin":0.0,
  "direction":"UP|DOWN|NONE",
  "calStatus":"NO_CALIBRATION|VALID|OUT_OF_RANGE",
  "calInRange":1,
  "onBottom":false,
  "currentLayer":1,
  ... calibration table ...,
  "witsCorrectionFt":0.5,
  "encoderPolarity":1,
  "uptime_s":123,
  "sequence":13,
  "crc":N
}
```

**Every 5th report** (≈ every 500 ms, `SENSOR_BROADCAST_PERIOD_MS`) the
firmware additionally appends the **16-channel analog sensor bank** between the
measurement summary and `uptime_s`:

```
"hookloadWindowMs":2500,
"sensor1":2.61, "sensor2":0.75, ...  "sensor16":5.000,   // VOLTS, 3 d.p.
"sensorStatus":"NNNNNNNNLLNHFNNH",
"uptime_s":123,
```

- `"sensorN"` = the **corrected input voltage** (V) of Sensor N−1. `sensor1`
  is Sensor 0 → the hookload chain's filtered A0 voltage. `sensor16` is a
  fully independent key — `sensor1` and `sensor16` can never collide.
- `"sensorStatus"` is a compact **16-char** string, `char[i]` = status of
  Sensor `i` (index 0 == Sensor 0 == `sensor1`), using the vocabulary:

  | char | meaning                        | char | meaning     |
  |------|--------------------------------|------|-------------|
  | `N`  | NORMAL                         | `F`  | FAULT       |
  | `L`  | LOW (below `min_eng`)          | `D`  | DISCONNECTED|
  | `H`  | HIGH (above `max_eng`)         | `C`  | CALIBRATION |
  | `I`  | INVALID                        | `X`  | NO_DATA     |
  | `U`  | UNUSED (channel disabled)      |      |             |

- A channel disabled via `sensor_set_param … param "enabled"` is **never
  sampled** by the firmware; its `sensorN` voltage key is emitted as `0.000`
  and its status char is `U` (UNUSED) so the dashboard never mistakes a
  floating unused input for a live reading.

- Sensor 0's status char is derived from the hookload subsystem's own loop
  diagnosis (`VALID`→`N`, `SENSOR_FAULT`→`F`, `OVER_RANGE`→`H`,
  `NO_CALIBRATION`→`C`, else `I`).
- Reports that do **not** carry the sensor block simply omit the two fields;
  a host must treat a missing `sensorStatus` as "no data yet".

### 4.3 `ack` — the command reply

```
{
  "type":"ack",
  "action":"set_calibration_point|set_wits_correction|save_calibration|
            load_calibration|restore_defaults|clear_calibration_point|
            set_encoder_polarity|sensor_set_param|confirm|...",
  "state":"awaiting_confirm|done|rejected|error",
  "value":<echo>?,            // 4 d.p., present when the staged value is meaningful
  "ec":<code>?,               // 0 on awaiting_confirm; code on rejected/error
  "req_id":n,                 // present only when the request had one
  "detail":"<reason>",
  ... measurement summary ...,   // present on done (and awaiting_confirm)
  "uptime_s":123,
  "sequence":14,
  "crc":N
}
```

- **`awaiting_confirm`** — requested value validated and **staged**, not yet
  applied. The host must follow with `confirm`.
- **`done`** — change **applied and persisted**. The ONLY state a host may
  treat as success. Because it echoes the ACTIVE config (the measurement
  summary), the host must **numerically verify** the echoed
  `calPositionN`/`calCounterN`/`witsCorrectionFt` matches what was requested
  before showing SAVED.
- **`rejected`** — the device refused the value on validation; **nothing
  changed**. `detail` carries the reason, `ec` the numeric code.
- **`error`** — malformed or logically impossible command; **nothing changed**.

### 4.4 `reset_ack` — immediate reset confirmation

```
{
  "type":"reset_ack",
  "currentTicks":0,
  "source":"BUTTON|COMMAND",
  "uptime_s":123,
  "sequence":15,
  "crc":N
}
```

`currentTicks` is hard-`0` by construction. `source` distinguishes the
physical reset button (`BUTTON`) from the dashboard command (`COMMAND`).

### 4.5 `log`

```
{ "type":"log", "message":"RESET BUTTON|CALIBRATION applied by operator|firmware boot OK|...",
  "uptime_s":123, "sequence":16, "crc":N }
```

### 4.6 Measurement summary (shared block)

Appended by the firmware to every `status` and every `ack` so the host always
verifies against reality.

```
"currentTicks":4437, "blockPositionFt":31.58, "velocityFtMin":0.0,
"direction":"STOPPED", "calStatus":"VALID", "calInRange":1,
"onBottom":false, "currentLayer":1,
"calCounter1":4437, "calPosition1":31.58,
"calCounter2":7988, "calPosition2":63.21,
"calCounter3":11228,"calPosition3":94.78,
"calCounter4":13199,"calPosition4":111.00,
"countsPerFoot1":112.10, "countsPerFoot2":112.50,
"countsPerFoot3":110.00, "countsPerFoot4":0.00,
"witsCorrectionFt":0.5, "encoderPolarity":1
```

Unconfigured rows report `calCounterN:0` and `calPositionN:0.00` (and their
`countsPerFootN:0.00`). `encoderPolarity` is `+1` or `-1`.

---

## 5. Error codes (`ec`)

Sent on every `ack` that is not `state:"done"` (and on `awaiting_confirm`,
where `ec:0`). If the field is absent, treat it as `0`.

| `ec` | Name             | Meaning                                            |
|------|------------------|----------------------------------------------------|
|  0   | `OK`             | No error (used on `awaiting_confirm`)              |
|  1   | `UNKNOWN`        | Unrecognized `cmd` verb                            |
|  2   | `PARAM`          | Malformed JSON / missing `cmd` field               |
|  3   | `VALUE`          | `value` missing or not numeric                     |
|  4   | `OUT_OF_RANGE`   | Numeric value outside the accepted range           |
|  7   | `NO_PENDING`     | `confirm` issued with nothing staged               |
|  8   | `OUT_OF_MEMORY`  | (internal) outbound buffer truncated               |
|  9   | `CAL_DUP_PULSES` | Calibration point uses an encoder counter already taken by another level |
| 10   | `CAL_DUP_POSITION` | Two calibration levels share the same physical position |
| 11   | `CAL_ZERO_INTERVAL` | Adjacent counter anchors would create a zero-length interval (singularity) |
| 12   | `CAL_BAD_ORDER`  | Counter and position orders conflict across anchors (physics impossible) |
| 13   | `CAL_FEW_POINTS` | Not enough valid points for a valid interval        |
| 14   | `CAL_BAD_POLARITY` | `set_encoder_polarity` received a value other than `+1`/`-1` |

> A host MUST treat `rejected`/`error` as "not applied" even if `detail` is
> empty — the `state` field is authoritative.

---

## 6. Validation rules (firmware side)

The firmware never trusts input (Serial or EEPROM):

- **`set_calibration_point`**: `layer` in `1..4` else `ec:4`; numeric `value`
  required (`ec:3`); value within the position range
  `[-100000, 1,000,000]` else `ec:4`. The table is kept **sorted by counter**
  at apply time. A point is **validated against the other configured points**
  before it is staged/applied (see calibration validation below).
- **`set_wits_correction`**: numeric `value` (`ec:3`) within the position
  range else `ec:4`.
- **`set_encoder_polarity`**: `value` must be `+1` or `-1`; anything else is
  `rejected` with `ec:14` (`CAL_BAD_POLARITY`).
- **Calibration validation** (`encoder_validate_calibration_point`): when a
  point is added/edited it is checked against the other rows and rejected with
  a stable code — duplicate **counter** (`ec:9`), duplicate **position**
  (`ec:10`), a **zero-length** counter interval between adjacent anchors
  (`ec:11`), and counter order disagreeing with position order across the
  span (`ec:12`, physics impossible). Editing/re-asserting an existing point
  is always allowed; the first point on an empty table is always accepted.
  A table with too few valid points for an interval yields `ec:13`.
- **`clear_calibration_point`**: `layer` in `1..4` else `ec:4`.
- **EEPROM**: every block is guarded by a magic marker and a Fletcher-16
  checksum plus finite/range sanity; a mismatch (or missing magic) causes a
  fallback to defaults rather than trusting corrupt bytes. The calibration
  auto-persists on **every** change (set point, clear, restore, counter reset
  re-base, polarity change).

---

## 7. Measurement model

The firmware is the **single authoritative** measurement engine. The encoder
is an internal position-counting sensor — there are **no** encoder hardware
parameters (PPR / decoding mode / gear ratio / wheel diameter / a single
`countsPerFoot`) anywhere.

The operator defines a **piecewise-linear calibration table** of up to
`MAX_CAL_POINTS` (4) anchor points `(calCounter, calPosition)`, sorted by
counter. The firmware counts quadrature edges from the encoder, applies the
**direction polarity** scalar `encoderPolarity` (`+1`/`-1`) to every edge, and
derives `calCounterN`. Between two consecutive anchors the position is linear:

```
blockPosition = calPosition1 | etc.
  given anchors (C1,P1),(C2,P2) with C1 <= count <= C2:
  position = P1 + (count - C1) / (C2 - C1) * (P2 - P1)
```

- Each interval has its own **counts/foot** `countsPerFootN = (C2-C1)/(P2-P1)`
  (a derived value, also emitted for display).
- When `count` is **outside** the calibrated span the firmware never
  extrapolates: it flags `calStatus:"OUT_OF_RANGE"` and `calInRange:0` and
  continues reporting `blockPositionFt` as the nearest trusted anchor.
- With **no anchors** configured, `calStatus:"NO_CALIBRATION"` and
  `calInRange:0`.
- The reported `blockPositionFt` = interpolated position **plus** the
  operator `witsCorrectionFt` offset. Velocity and direction are **unaffected**
  by the offset.

**Velocity** (`velocityFtMin`) is the block-position delta over one data
period (100 ms), EMA-smoothed (`VELOCITY_SMOOTH`), in ft/min. **Direction** is
`UP`/`DOWN` while motion is recent (`DIRECTION_TIMEOUT_MS` = 6 s) and
`|velocity| ≥ VELOCITY_STOP_EPS`, else `STOPPED`. **`ON BOTTOM`** is a
direction lamp state: `ON BOTTOM` is shown when the direction is `STOPPED` and
the reported position is at or below the **lowest** calibrated anchor plus a
small epsilon (`ON_BOTTOM_EPS_FT` = 0.75 ft).

**Current layer** (`currentLayer`) = the 1-based table row of the anchor
`calCounter[i]` that is **at or below** the live encoder counter (highest such
row); if none qualifies, the block belongs to **layer 1**. Because the
calibration is enforced monotonic (BAD_ORDER rejected), this counter-band rule
is unambiguous and follows the PULSES the operator enters per layer — count ≥
a layer's counter anchor means that layer (or a higher one).

There is **exactly one** position calculation in the whole system. The removed
model (`countsPerFoot`, `initialTapeReading`, `referencePositionFeet`,
`referenceTicks`, and the older `PULSES_PER_REV`/`PULSES_PER_FOOT` math) does
not exist anywhere in the firmware or dashboard.

### Reset vs Restore

| Action            | Effect                                                      |
|-------------------|-------------------------------------------------------------|
| **RESET COUNTER** | `currentTicks = 0` with position preserved by a **runtime reference** (tick offset = pre-reset ticks, position offset = 0). **Never** re-bases, modifies, saves, erases or deletes the calibration table or WITS offset. Replies `reset_ack` with `currentTicks:0` + preserved `blockPositionFt`. |
| **RESET FEET** | `currentTicks = 0`, `blockPositionFt = 0.00`, velocity `0.00`, direction `STOPPED`, via a runtime reference (base tick + base position). **Never** modifies the calibration table or WITS. Replies `reset_feet_ack` with `currentTicks:0` + `blockPositionFt:0.0`. |
| **RESTORE DEFAULTS** | Clears the calibration table + WITS offset **and resets polarity to `+1`** to factory defaults and persists them. **Never** touches the live counter. Two-stage via `confirm`. |
| **DELETE LEVEL** | Removes **one** calibration row (`calCounterN`/`calPositionN` → `0`/`0.0`) and persists. **Never** touches the live counter. Two-stage via `confirm`. |

---

## 8. Host integration notes

1. On connect, send `{"cmd":"status"}` and display nothing until the verified
   `status` snapshot arrives.
2. Treat a setting as applied **only** after `state:"done"` and a numeric
   match of the echoed value (tolerance `VERIFY_TOL` ≈ 0.002 relative).
3. Show **PENDING** from the moment the request is sent until the verified
   `done` ack; **SAVED** only then; **FAILED** on rejection or
   `CMD_ACK_TIMEOUT_S` (3 s) timeout, or on link loss. Never be optimistic.
4. Show **RESET SUCCESSFUL** only after a `reset_ack` with `currentTicks:0`.
   The calibration is preserved byte-for-byte (never re-based), so do not show
   a "cleared configuration" message on reset.
5. Track `uptime_s`: a decrease means the device rebooted; re-assert settings
   after a reboot.
6. Data (`report`) arrives every 100 ms. Missing frames for > a few seconds
   ⇒ stale/lost link; `uptime_s`/`sequence` allow exact frame audit.

---

## 9. Version history of the protocol

- **4.2** — 16-channel analog sensor bank (Sensor 0..15). The periodic
  `report` carries the live bank every 500 ms: self-describing voltage keys
  `sensor1`..`sensor16` plus the compact position-indexed `sensorStatus` string
  (index 0 == Sensor 0). New commands: `sensor_set_param` (two-stage,
  one channel of Sensor 1..15; `sensor`+`param`+`value`, persisted to EEPROM),
  `sensor_reset` (immediate, live-state only), `sensor_factory_reset`
  (immediate, restores Sensor 1..15 defaults; Sensor 0 is hookload-owned and
  untouched). Command dispatch now parses fields by targeted re-scan
  (`req_id` stays bit-exact long).
- **4.1** — Configurable encoder direction polarity, single-level delete, and
  calibration validation. New commands `set_encoder_polarity` (two-stage,
  `value` `+1`/`-1`, persisted, echoed as `encoderPolarity` in the summary) and
  `clear_calibration_point` (two-stage, deletes ONE calibration level without
  touching the live counter). Calibration points are validated on
  stage/apply — duplicates (`ec:9`/`ec:10`), zero-length intervals (`ec:11`),
  counter/position order conflicts (`ec:12`), few valid points (`ec:13`), and
  bad polarity (`ec:14`) are rejected with stable codes. `restore_defaults`
  now also resets polarity to `+1`.
- **4.0** — Multi-point piecewise-linear calibration model. The single
  `countsPerFoot`/`initialTapeReading`/`referencePositionFeet`/`referenceTicks`
  model is replaced by a 4-anchor table (`calPosition1..4`, `calCounter1..4`,
  per-interval `countsPerFoot1..4`) plus `witsCorrectionFt`. Commands become
  `set_calibration_point`, `set_wits_correction`, `save_calibration`,
  `load_calibration`, `restore_defaults`, `confirm`, `reset_counter`, `status`.
  State adds `calStatus`, `calInRange`, `onBottom`. Removed:
  `set_counts_per_foot`, `set_initial_tape_reading`, `set_block_position`,
  `set_layer_ref`.
- **3.0** — Block Position Monitor redesign. `status`/`report`/`ack`/
  `reset_ack`/`log` JSON messages with the operator-defined measurement
  model. The old ASCII `SETCAL`/`SETLIMIT`/`ZERO`/`RESTORE` verbs and the
  encoder-calibration model are removed.
- **2.0** — numeric error codes; fixed outbound buffers (pre-redesign).
- **1.x** — original request→confirm→echo (pre-redesign).
