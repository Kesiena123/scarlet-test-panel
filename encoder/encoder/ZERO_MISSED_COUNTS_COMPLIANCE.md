# Zero Missed Counts — Compliance & Verification Notes

Relates to: `promt1.txt` integrity requirement — at normal duty the encoder
must not miss or invent any transition.

## 1. Why the encoder must miss nothing

The whole measurement model is piecewise-linear between the operator's
calibration anchors (count, position) pairs:

```
Between anchors (C1,P1),(C2,P2):  position = P1 + (count - C1)/(C2 - C1)*(P2 - P1)
```

Every missed or invented transition changes `count` by at least `±1`, so it
shifts every reported `blockPositionFt` permanently by
`±1 / per-interval countsPerFoot` (for the demo anchors, ≈ 0.009 ft) until the
operator re-pins a calibration point (`set_calibration_point`). Misses are
therefore **accumulating, unreported positional errors** — not a pretty-number
problem. The per-interval `countsPerFoot` values are operator-defined and never
intrude on the counting path: the ISR only ever adds `+1`/`-1` per **legal**
quadrature edge.

## 2. Requirements enforced in firmware (`encoder.cpp`, `encoder.ino`)

- **Dedicated hardware quadrature ISR** (`encoder_isr`, `IRAM_ATTR` = no-op on
  AVR; already in RAM): fires only on change of `pin ENC_QUAD_A` or
  `pin ENC_QUAD_B` and runs to completion quickly (constant-time table lookup,
  no allocation, no serial I/O).
- **Complete 4×4 state transition table** (`s_translate[oldState][newState]`,
  16 entries): only the four legal state changes advance the count
  (`+1`/`-1`), every diagonal (impossible no-op) yields `0`, and **all other
  illegal transitions are counted** — `/4`/`*4`/`*/2`/`/2` pins are detected
  and bump `g_illegal_count`, never silently incrementing the count.
- **Torn-read safe 32-bit counter**: every read outside the ISR is guarded by
  `noInterrupts()/interrupts()` (`encoder_read_counter`); writes are atomic
  (`encoder_set_counter`). The operator reset and the loop() can never observe
  a partial count.
- **Debounced reset capture with tail-debounce** (`encoder.ino`): on-press
  latches `g_reset_requested`; after debounce the ISR/loop turns the counter
  to 0 and the firmware re-shapes the "reset tail" into a normal trailing
  edge so a physical button bounce cannot inject phantom counts afterwards.
- **Activity decay** (`encoder_process`): elapsed time decays toward zero in
  EMA steps every `STATUS_PERIOD_S`; no counters are ever reset by the decay.
- **Interrupts are NEVER disabled in the loop path** except for the few-
  microsecond atomic counter read/write (`encoder_read_counter` /
  `encoder_set_counter`). All serial, EEPROM, and command handling leaves the
  ISR enabled at all times.
- **No missed-count recovery in the firmware**: `currentTicks` counts **every**
  legal edge, exactly. There is no division, no PPR/gear/wheel math, no single
  `countsPerFoot`-based increment, and no re-sync polling anywhere near the
  count.

## 3. Why no count can be lost between ISR and EEPROM

`currentTicks` is a `volatile long` that the ISR alone ever increments. The
autosave path (`encoder_autosave_process`) copies it atomically
(`encoder_read_counter()`) into a buffer before writing EEPROM; the *stored*
value is only ever used to rebuild the counter on power-up (and to report the
current counter), never as the live count. So the persisted value can trail
the live count by at most the autosave period — the live count never loses a
tick to a write.

## 4. Self-test & verification hooks (dashboard/Firmware)

- **Illegal-transition counter** — `encoder_illegal_transitions()` /
  `clear_illegal_transitions()`. Wrapping the encoder drive with high-speed
  transitions and reading this counter proves no edge was dropped.
- **Deterministic quadrature brute-force (lab)**: drive all 16⇉16 pin-state
  transitions while capturing the count and the illegal counter. From any
  state only two legal one-step transitions exist; the table `s_translate`
  maps exactly those to `±1` and everything else either to `0` (diagonal) or
  to an illegal increment.
- **Audit correlation**: every firmware line carries `uptime_s` and
  `sequence`. A host can count `report` frames and waste NO cycles on the
  assumption that a missing frame is "just a frame drop" — with this device
  each 100 ms frame is checksummed; a missing frame is detectable through
  `sequence` gaps.

## 5. Acceptance cross-reference (promt1.txt)

The lab "Quadrature integrity" and "Illegal transition" acceptance rows are
pinned exactly to the checks above (4×4 `s_translate` completeness + illegal
counter + torn-read-safe access to `g_currentTicks`). See
`VERIFICATION_TEST.py` (grey-box sources) and `ACCEPTANCE_TEST.py`
(black-box operator harness) in `encoder/`.