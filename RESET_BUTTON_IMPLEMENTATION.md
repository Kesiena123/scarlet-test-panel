# Reset Button Implementation (Block Position Monitor, firmware 4.1.0)

Spec: promt1.txt (reset) and (reset vs restore). The operator and the
dashboard must be able to **zero the block counter** at a jog position, while
the operator-entered calibration (the multi-point anchor table and the WITS
offset) must survive any reset.

## 1. Behaviour contract

| Input | Effect on | Preserved |
|-------|-----------|-----------|
| **Physical button** (D4, active-low to GND) | `currentTicks = 0` + establishes a **runtime reference** (`tick_offset = pre-reset ticks`, `pos_offset = 0`) + EEPROM counter block | the calibration anchors `calPositionN`, `calCounterN` (byte-for-byte, untouched), `witsCorrectionFt` — **never re-based** |
| **Dashboard `reset_counter`** | same | same |

Both paths produce the identical reset result; the only difference visible to
the dashboard is the `source` field of the `reset_ack`
(`"BUTTON"` vs `"COMMAND"`).

A reset **preserves position via a runtime reference** rather than touching the
calibration table: it records the pre-reset tick count (and, for RESET FEET,
the pre-reset position) into runtime globals, zeros the live counter, and
computes the displayed position from `effectiveTicks = counter + offset`. The
calibration table (`calCounterN`/`calPositionN`) and WITS offset are **never**
modified by reset — they stay byte-for-byte identical (clearing them is a
separate **Restore Defaults → CONFIRM** operation). Nothing about a reset can
change what the operator calibrated.

Deleting a single calibration row is **also** a separate operation —
**Delete Level** (`clear_calibration_point`) removes one `calCounterN`/
`calPositionN` pair and never touches the live counter. Reset and delete-level
are fully independent (promt2.txt).

## 2. Hardware & wiring

- Pin: `D4` (`PIN_RESET_BUTTON`), `INPUT_PULLUP`, momentary button to GND.
- No external pull-up/pull-down or level conversion required on the Mega.
- Debounce window: `RESET_DEBOUNCE_MS = 50` ms.

## 3. Firmware implementation (encoder.ino loop)

1. **Read** the button (polled every loop turn; low = pressed).
2. **Debounce**: only after `RESET_DEBOUNCE_MS` of stable state does the
   press latch `g_reset_requested = true` (volatile, set by the ISR-side
   capture too).
3. In `loop()`-driven handling the reset executes once:
   - `encoder_execute_reset()` — atomic counter zero
     (`noInterrupts/interrupts`-guarded write to `g_currentTicks`) that
     establishes a **runtime reference** (`g_runtime_tick_offset = delta`,
     `g_runtime_pos_offset = 0.0`, `g_runtime_offset_active = true`). The
     displayed position is preserved by the runtime offset; the calibration
     table is **never re-based, modified or saved**. The removed
     `encoder_rebase_calibration(-currentTicks)` is gone from the codebase.
   - EEPROM counter block is rewritten synchronously (guarded blocks, see
     `CALIBRATION_DEFAULTS.md`).
   - The `reset_ack` JSON message
     (`type="reset_ack"`, `currentTicks:0`, `source:"BUTTON"|"COMMAND"`,
     `uptime_s`, `sequence`, Fletcher-16 CRC) is emitted.
4. **Tail-debounce**: right after executing the reset the firmware reshapes
   the button's *released* edge with the same 50 ms debounce so a bounce on
   release can never re-trigger.

## 4. Why a button bounce cannot inject counts

- Quadrature counting is ISR-driven and independent of the button.
- The reset only ever **writes** the counter to 0; any post-reset legal
  encoder edge increments it normally.
- The debounce window prevents a chattering button from being read as
  repeated presses — exactly one `reset_ack` is emitted per physical press.

## 5. Dashboard handling

`scarlet_test_panel/main.py` `_handle_reset_ack` and
`services/commands.py` `on_reset_ack`:

- A reset is reported as **successful only** when a `reset_ack` arrives and
  `currentTicks == 0` (guarded with
  `raw is not None and int(float(raw)) == 0` — a missing field or `0.0`
  formatting never mis-evaluates; `0` as a value is never falsy-trapped).
- On success the banner shows **RESET SUCCESSFUL — counter zeroed**, the
  status bar counter shows `0`, and the pending flag is cleared.
- If no `reset_ack` arrives within `CMD_ACK_TIMEOUT_S` (3 s), the dashboard
  marks the reset FAILED (never optimistic).
- The physical button produces the same `reset_ack`; both sources follow the
  same verified-success path. The calibration table is preserved byte-for-byte
  (never re-based), so the dashboard does **not** show a "cleared
  configuration" message on reset.

After a reset there is no fresh block-position `report` until the next
100 ms data cycle, so the displayed `blockPositionFt` remains the **last
verified** value until then — this is correct and intended.

## 6. Verification references

- Grey-box checks: `encoder/VERIFICATION_TEST.py` (debounce constant, tail
  handling, `reset_ack` `currentTicks:0`, EEPROM blocks not erased).
- Manual checklist: press the button → counter → 0 in < 100 ms, banner shows
  successful, settings/layers unchanged, ≤ 1 `reset_ack` per press, restored
  value after a brown-out reboot stays at 0 (counter persisted).