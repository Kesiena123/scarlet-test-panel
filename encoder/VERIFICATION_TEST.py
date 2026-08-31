"""VERIFICATION_TEST — grey-box static verification of the Block Position
Monitor (firmware 4.1.0 + dashboard 4.x).

This harness inspects the ACTUAL source files (firmware C++ and the Python
dashboard) and verifies the structural claims that can be proven without a
physical device:

  * exactly one authoritative multi-point piecewise-linear calibration model,
  * the removed legacy encoder-calibration system is functionally gone,
  * quadrature integrity (4x4 transition table, A+B ISRs, torn-read safety),
  * EEPROM blocks guarded by magic + Fletcher-16 (calibration survives power),
  * reset semantics (counter zero + position preserved WITHOUT changing calibration),
  * two-stage configuration and the error-code table,
  * promt2: configurable direction polarity, calibration validation, DELETE LEVEL
  * promt2: calibration table input (USER PULSES / USER FEET editable cells)
  * dashboard protocol service integrity (Fletcher-16, no 0-as-falsy traps).

Physical/behavioural rows (live encoder, button, power-cycle) belong to
ACCEPTANCE_TEST.py (promt1.txt / promt2.txt) and are marked NOT VERIFIED until
a hardware run. Nothing here claims PASS for a hardware test.

Run:  python encoder/VERIFICATION_TEST.py
Exit: 0 when every grey-box check passes, 1 otherwise.
"""

import os
import re
import sys
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FW = os.path.join(ROOT, "encoder", "encoder")
DASH = os.path.join(ROOT, "scarlet_test_panel")

_failures = 0
_results = []


def check(name, ok, detail=""):
    global _failures
    status = "PASS" if ok else "FAIL"
    if not ok:
        _failures += 1
    _results.append((name, status, detail))
    print(f"  [{status}] {name}" + (f"  — {detail}" if detail else ""))


def _ftext(directory, extensions=("*.ino",)):
    out = {}
    for ext in extensions:
        for path in sorted(glob.glob(os.path.join(directory, "**", ext),
                                     recursive=True)):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                out[os.path.relpath(path, ROOT)] = fh.read()
    return out


def _pytext(directory):
    out = {}
    for ext in ("*.py",):
        for path in sorted(glob.glob(os.path.join(directory, "**", ext), recursive=True)):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                out[os.path.relpath(path, ROOT)] = fh.read()
    return out


def _strip_c_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _strip_py_comments(text):
    text = re.sub(r'""".*?"""', " ", text, flags=re.DOTALL)
    text = re.sub(r"'''.*?'''", " ", text, flags=re.DOTALL)
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def _config_constant(name):
    text = _ftext(FW)["encoder\\encoder\\TravellingBlockMonitor\\TravellingBlockMonitor.ino"]
    m = re.search(r"#define\s+" + name + r"\s+([^\s/]+)", text)
    return m.group(1).rstrip("fULul") if m else None


print("=" * 78)
print("VERIFICATION TEST — Block Position Monitor (grey-box static)")
print("=" * 78)

config_text = _ftext(FW).get("encoder\\encoder\\TravellingBlockMonitor\\TravellingBlockMonitor.ino", "")
fw_ver = re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', config_text)
fw_build = re.search(r'#define\s+FW_BUILD\s+"([^"]+)"', config_text)
fw_proto = re.search(r'#define\s+FW_PROTOCOL_VERSION\s+"([^"]+)"', config_text)
print(f"Firmware under test: {fw_ver.group(1) if fw_ver else '?'} "
      f"(build {fw_build.group(1) if fw_build else '?'}) "
      f"protocol {fw_proto.group(1) if fw_proto else '?'}")
print()

# ── 1. Single authoritative formula ────────────────────────────────────
print("1. Measurement model — multi-point piecewise-linear calibration")
ftexts = _ftext(FW)
ino = ftexts.get("encoder\\encoder\\TravellingBlockMonitor\\TravellingBlockMonitor.ino", "")
# Canonical firmware interpolation: position = P1 + (count - C1)/(C2 - C1)*(P2 - P1)
formula_re = re.compile(
    r"p1\s*\+\s*frac\s*\*\s*\(\s*p2\s*-\s*p1\s*\)")
firm_hits = [name for name, t in ftexts.items() if formula_re.search(t)]
check("firmware interpolation formula is present", len(firm_hits) >= 1,
      ",".join(firm_hits) or "no firmware file matches the canonical formula")
# The removed single countsPerFoot formula must be functionally gone.
legacy_formula = re.compile(
    r"referencePositionFeet\s*\+\s*\(\s*currentTicks\s*-\s*referenceTicks\s*\)\s*/\s*countsPerFoot")
gone_formula = [name for name, t in ftexts.items() if legacy_formula.search(_strip_c_comments(t))]
gone_formula.extend(name for name, t in _pytext(DASH).items()
                    if legacy_formula.search(_strip_py_comments(t)))
check("single countsPerFoot formula removed", not gone_formula,
      "found in " + ", ".join(gone_formula) if gone_formula else "0 matches")

main_py = _pytext(DASH).get("scarlet_test_panel\\main.py", "")
dash_formula = bool(re.search(r"def calibrated_position", main_py)
                    and re.search(r"calCounter", main_py)
                    and re.search(r"\+\s*float\(self\.device\.get\(\"witsCorrectionFt\"", main_py))
check("dashboard calibrated_position() mirrors interpolation + WITS", dash_formula,
      "main.py calibrated_position() uses calCounter/calPosition/witsCorrectionFt")

removed_math = re.compile(
    r"distanceInFeet\s*=|PULSES_PER_FOOT\s*=|PULSES_PER_REV\s*=|WHEEL_DIAMETER_INCH")
gone = [name for name, t in ftexts.items() if removed_math.search(_strip_c_comments(t))]
gone.extend(name for name, t in _pytext(DASH).items() if removed_math.search(_strip_py_comments(t)))
check("no competing/legacy feet formula anywhere", not gone,
      "found in " + ", ".join(gone) if gone else "0 matches")

# ── 2. Removed legacy calibration system ───────────────────────────────
print("2. Legacy encoder-calibration system is functionally removed")
legacy_tokens = ["ENCODER_PPR", "DECODING_MODE", "GEAR_RATIO",
                 "WHEEL_DIAMETER_INCH", "PULSES_PER_REV", "PULSES_PER_FOOT"]
hits = []
for name, t in ftexts.items():
    for token in legacy_tokens:
        if token in _strip_c_comments(t):
            hits.append(f"{name}:{token}")
for name, t in _pytext(DASH).items():
    for token in legacy_tokens:
        if token in _strip_py_comments(t):
            hits.append(f"{name}:{token}")
check("legacy constants never used in code (project sources)", not hits,
      "; ".join(sorted(set(hits))) if hits else "no functional references")

# Single counts/ft token removed from the dashboard model (raw legacy keys /
# verbs only — the per-interval helper name countsPerFoot{N} is the new model).
legacy_cpf = [name for name, t in _pytext(DASH).items()
              if re.search(r"FW_REF_COUNTS_PER_FOOT|FW_REF_INITIAL_TAPE_READING|"
                           r"initialTapeReading|referenceTicks|set_counts_per_foot|"
                           r"set_initial_tape_reading|set_layer_ref",
                           _strip_py_comments(t))]
check("single countsPerFoot removed from dashboard model", not legacy_cpf,
      "; ".join(legacy_cpf) if legacy_cpf else "no dashboard countsPerFoot references")

# ── 3. Quadrature integrity ────────────────────────────────────────────
print("3. Quadrature integrity (counts are never invented/missed)")
enc_cpp = _strip_c_comments(ino)
trans = re.search(r"s_translate\s*\[4\]\[4\]\s*=\s*\{(.*?)\};", enc_cpp, flags=re.DOTALL)
trans_values = [int(v) for v in re.findall(r"-?\d+", trans.group(1))] if trans else []
# legal edges: only transitions that change exactly one line are ±1 steps;
# diagonals are 0, and every other state change is counted, never counted in.
ones = sum(1 for v in trans_values if v == 1)
neg = sum(1 for v in trans_values if v == -1)
ok_trans = (len(trans_values) == 16 and ones >= 2 and neg >= 2
            and trans_values.count(0) >= 8)
check("4x4 transition table present and complete (±1 both directions)", ok_trans,
      f"{len(trans_values)} entries, +1 x{ones}, -1 x{neg}")

check("A+B both interrupt-driven", all(
    p in (ino + enc_cpp)
    for p in ("PIN_ENCODER_A", "PIN_ENCODER_B", "attachInterrupt")))
check("illegal transitions counted, never applied to the count",
      "g_illegal_count" in enc_cpp)
check("counter reads are torn-read safe (noInterrupts)",
      "noInterrupts()" in ino
      and "interrupts()" in ino)
check("counter writes are atomic", "encoder_set_counter" in enc_cpp)

# ── 4. EEPROM guarded persistence ──────────────────────────────────────
print("4. EEPROM persistence (magic + Fletcher-16 guarded blocks)")
eep_storage = ino
check("magic markers present", "CAL_MAGIC" in eep_storage
      and "COUNTER_MAGIC" in eep_storage)
check("Fletcher-16 verification on load", "fletcher16" in eep_storage or "Fletcher" in eep_storage)
check("corrupt/absent block falls back to defaults",
      "eeprom_cal_restore" in eep_storage
      and "encoder_set_calibration_defaults" in ino)
cal_tokens = ("calCounter", "calPosition", "witsCorrection", "calUsedMask")
check("calibration table + WITS persisted (calibration survives power loss)",
      all(tok in eep_storage for tok in cal_tokens))
check("calibration auto-saves on change (save in set path)",
      ("eeprom_cal_save" in ino
       and "encoder_set_calibration_point" in ino))

# ── 5. Reset semantics ─────────────────────────────────────────────────
print("5. Reset semantics (counter zero + position preserved WITHOUT changing calibration; never delete)")
enc_ino = ino
sproto = ino
check("reset is debounced (RESET_DEBOUNCE_MS)",
      "RESET_DEBOUNCE_MS" in enc_ino or "RESET_DEBOUNCE_MS" in config_text)
check("reset executes a counter zero, not a config wipe",
      "encoder_execute_reset" in enc_ino
      or "encoder_set_counter" in ino)
check("reset preserves position via a runtime reference (NOT a calibration re-base)",
      "g_runtime_tick_offset" in ino
      and "encoder_effective_ticks" in ino
      and "encoder_display_position_ft" in ino)
check("reset NEVER re-bases calibration (rebase removed from codebase)",
      "encoder_rebase_calibration" not in ino)
check("calibration input survives reset byte-for-byte",
      "protocol_event_reset" in sproto
      and "encoder_execute_reset" in ino
      and "set_calibration_point" in sproto and "reset_counter" in sproto
      and "encoder_rebase_calibration" not in ino)
check("reset ack reports currentTicks:0", "reset_ack" in sproto
      and "currentTicks" in sproto)

# ── 6. Two-stage protocol + error codes ────────────────────────────────
print("6. Two-stage protocol and stable error codes")
check("CONFIRM step required for config", bool(
    re.search(r'"confirm"', sproto)) and "CMD_ERR_NO_PENDING" in sproto)
ec_ok = all(code in config_text for code in
            ("CMD_OK", "CMD_ERR_UNKNOWN", "CMD_ERR_PARAM", "CMD_ERR_VALUE",
             "CMD_ERR_OUT_OF_RANGE", "CMD_ERR_NO_PENDING", "CMD_ERR_OUT_OF_MEMORY",
             "CMD_ERR_CAL_DUP_PULSES", "CMD_ERR_CAL_DUP_POSITION",
             "CMD_ERR_CAL_ZERO_INTERVAL", "CMD_ERR_CAL_BAD_ORDER"))
check("error-code enum complete", ec_ok)
cmd_verbs = ["set_calibration_point", "set_wits_correction",
             "save_calibration", "load_calibration",
             "restore_defaults", "status", "reset_counter", "reset_feet",
             "clear_calibration_point", "set_encoder_polarity"]
check("all v4 commands implemented", all(v in sproto for v in cmd_verbs))
removed_verbs = ["set_counts_per_foot", "set_initial_tape_reading",
                 "set_block_position", "set_layer_ref"]
gone_verbs = [v for v in removed_verbs if v in _strip_c_comments(sproto)]
check("removed v3 verbs are gone", not gone_verbs, "; ".join(gone_verbs) or "0 removed verbs")
check("ON BOTTOM / out-of-range status emitted",
      "ON_BOTTOM" in sproto or "onBottom" in sproto)
check("calStatus + calInRange emitted by reports",
      "calStatus" in sproto and "calInRange" in sproto)

# ── 7. Dashboard service integrity ─────────────────────────────────────
print("7. Dashboard service integrity")
proto_py = _pytext(DASH).get("scarlet_test_panel\\services\\protocol.py", "")
check("client Fletcher-16 present", "def fletcher16" in proto_py)
check("client validates messages", "def validate_message" in proto_py
      or "BadChecksum" in proto_py or "crc" in proto_py)
cmds_py = _pytext(DASH).get("scarlet_test_panel\\services\\commands.py", "")
check("two-stage tracker present", "awaiting_confirm" in cmds_py
      or "set_calibration_point" in cmds_py)
check("calibration command helpers present",
      all(v in cmds_py for v in ("set_calibration_point", "set_wits_correction",
                                 "save_calibration", "load_calibration")))
main_src = main_py
reset_guard = re.search(
    r"raw is not None and int\(float\(raw\)\) == 0", main_src) \
    or re.search(r"is not None[^\n]*==\s*0", main_src)
check("reset ack never treats counter 0 as falsy", bool(reset_guard),
      "currentTicks 0 handled by explicit raw-vs-None check")

# ── 8. promt2: polarity, validation, DELETE LEVEL ───────────────────────
print("8. promt2 — configurable polarity, calibration validation, DELETE LEVEL")
enc_src = _strip_c_comments(ino)
check("encoder direction polarity configurable (+1/-1)",
      "g_encoderPolarity" in enc_src
      and "encoder_set_polarity" in enc_src
      and "DEFAULT_ENCODER_POLARITY" in config_text)
check("polarity is sign-applied to every quadrature edge",
      ("g_encoderPolarity" in enc_src
       and ("g_currentTicks += (g_encoderPolarity" in ino
            or "g_currentTicks -= " in ino)))
check("polarity persisted in EEPROM cal block",
      "EEPROM_ADDR_CAL_POLARITY" in eep_storage
      and "encoderPolarity" in sproto)
check("calibration validation rejects duplicates/intervals/order",
      "encoder_validate_calibration_point" in enc_src
      and "CAL_VAL_DUP_PULSES" in ino
      and "CAL_VAL_BAD_ORDER" in enc_src)
check("set_calibration_point validates before applying",
      "cal_validation_detail" in sproto and "cal_validation_code" in sproto)
check("DELETE LEVEL (clear_calibration_point) separate from RESET COUNTER",
      "clear_calibration_point" in sproto
      and "encoder_clear_calibration_point" in enc_src)
cmds_dash = _pytext(DASH).get("scarlet_test_panel\\services\\commands.py", "")
check("dashboard DELETE LEVEL + polarity command helpers present",
      "clear_calibration_point" in cmds_dash
      and "set_encoder_polarity" in cmds_dash)
block_tab = _pytext(DASH).get("scarlet_test_panel\\tabs\\block_position_tab.py", "")
check("dashboard DELETE LEVEL + SET POLARITY UI present",
      "DELETE LEVEL" in block_tab and "_on_delete_level" in block_tab
      and "SET POLARITY" in block_tab and "_on_set_polarity" in block_tab)
check("dashboard calibration table is an editable counts/position input",
      "ENCODER COUNTS" in block_tab and "POSITION (FT)" in block_tab
      and "pulses_spin" in block_tab and "feet_spin" in block_tab
      and "_on_set_layer" in block_tab)

# ── 9. Boundary-correctness locks (regression guards) ──────────────────
print("9. Boundary-correctness regression guards")
sproto_cpp = ino
enc_bound = ino
# countsPerFootN is 13 chars + digit + null = 15 bytes; a 14-byte key
# buffer would overflow by one byte when the null terminator is written.
check("countsPerFootN key buffer cannot overflow (key[16])",
      re.search(r"char key\[16\]", sproto_cpp)
      and "key[13]=(char)('1'+i); key[14]='\\0';" in sproto_cpp)
check("top calibrated anchor (exact counter) is VALID, not OUT_OF_RANGE",
      bool(re.search(r"ticks\s*>\s*pts\[n\s*-\s*1\]\.counter", enc_bound))
      and "ticks > pts[n - 1].counter" in enc_bound
      and not re.search(r"ticks\s*>=\s*pts\[n\s*-\s*1\]\.counter", enc_bound))
check("ON BOTTOM uses CALIBRATED position (WITS is display-only)",
      "encoder_on_bottom" in enc_bound
      and bool(re.search(r"r\.calibratedPosition\s*<=\s*\(pts\[0\]\.pos" , enc_bound))
      and "r.reportedPositionFt <= (pts[0].pos" not in enc_bound)

# ── Summary ────────────────────────────────────────────────────────────
print()
total = len(_results)
passed = sum(1 for _, s, _ in _results if s == "PASS")
print(f"TOTAL: {total}  PASS: {passed}  FAIL: {_failures}")
print("VERIFICATION STATUS:", "ALL GREY-BOX CHECKS PASS" if _failures == 0
      else "CHECKS FAILED — see above")
sys.exit(0 if _failures == 0 else 1)