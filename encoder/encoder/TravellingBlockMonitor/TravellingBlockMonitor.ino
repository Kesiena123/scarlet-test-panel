// ============================================================
//  TravellingBlockMonitor.ino
//  Block Position Monitor Firmware — Arduino Mega 2560
//
//  Single-file build combining all firmware modules:
//    - config.h          compile-time constants, EEPROM map, error codes
//    - encoder.h/.cpp    quadrature ISR + multi-point calibration model
//    - eeprom_storage.h/.cpp  power-loss persistence (magic + Fletcher)
//    - serial_protocol.h/.cpp  JSON wire protocol, velocity/direction/layer
//
//  Quadrature encoder as an INTERNAL position-counting sensor, with an
//  OPERATOR-DEFINED multi-point calibration model:
//
//      position = P1 + (currentCount - C1) / (C2 - C1) * (P2 - P1)
//
//  over a calibration table of up to MAX_CAL_POINTS (4) anchor points, each
//  an (encoderCount, positionFt) pair captured during CALIBRATE mode.
//
//  All fixed encoder hardware parameters from earlier systems
//  (ENCODER_PPR / DECODING_MODE / GEAR_RATIO / WHEEL_DIAMETER_INCH /
//  PULSES_PER_REV / PULSES_PER_FOOT) and the single-counts-per-foot formula
//  are REMOVED. Velocity is computed from real block-position deltas, never
//  motor RPM.
//
//  Device<->Dashboard Settings Protocol:
//    Every settings change is REQUEST -> CONFIRM -> ACK(echo).
//    The firmware echoes the value it ACTUALLY STORED and can REJECT an
//    implausible value with a numeric error code ("ec") plus human "detail".
//    reset_counter is IMMEDIATE (no CONFIRM): zeroes currentTicks and
//    preserves the position via a runtime reference. NEVER re-bases, clears,
//    saves or deletes the calibration table.
//    Calibration auto-persists to EEPROM on every change.
//
//  VERSION: 4.1.0 (2026-08-29)
// ============================================================


// ============================================================
//  SECTION 1: HEADER, LIBRARIES, AND INCLUDES
// ============================================================
#include <Arduino.h>
#include <EEPROM.h>
#include <stdlib.h>
#include <string.h>
#include <avr/wdt.h>


// ============================================================
//  SECTION 2: PIN DEFINITIONS
// ============================================================

// Interrupt-capable pins on the Mega: 2,3,18,19,20,21
// pinA = INT0, pinB = INT1. Pins are INPUT_PULLUP; quadrature
// signals are active-low against the pull-ups.
#define PIN_ENCODER_A        2
#define PIN_ENCODER_B        3

// Physical reset/tare input. Active-low momentary button to GND on D4,
// polled in loop() with software debounce (RESET_DEBOUNCE_MS).
// RESET zeros currentTicks and preserves the physical position via a runtime
// reference (the calibration table is NOT re-based, modified or saved). It
// does NOT delete the saved calibration (that would be RESTORE DEFAULTS).
#define PIN_RESET_BUTTON     4
#define RESET_DEBOUNCE_MS    50UL


// ============================================================
//  SECTION 3: COMPILE-TIME CONSTANTS
// ============================================================

// Portability: IRAM_ATTR is an ESP32-ism. On AVR (Arduino Mega) the ISR
// already lives in flash/RAM directly, so it is a no-op here.
#ifndef IRAM_ATTR
#define IRAM_ATTR
#endif

// ── Version / build (traceability for audits) ─────────────────
#define FW_VERSION           "4.1.0"
#define FW_BUILD             "2026-08-29"
#define FW_PROTOCOL_MAJOR    4
#define FW_PROTOCOL_MINOR    1
#define FW_PROTOCOL_VERSION  "4.1"

// Debug mode: count rejected (illegal) quadrature transitions so electrical
// noise on the encoder lines is easy to detect.
#define DEBUG_ILLEGAL_TRANSITIONS        1

// ENCODER_RAW_DEBUG — RAW ENCODER DIAGNOSTIC MODE (opt-in, OFF by default).
// ============================================================================
// When compiled with -DENCODER_RAW_DEBUG (or #define'd before upload), the
// firmware ALSO emits a dedicated serial diagnostic line that reports the raw
// Channel A / Channel B digital states SEPARATELY from the accumulated tick
// count:
//
//     E:A=1 B=0 TICK=1548
//
// so the operator can instantly tell (a) whether A and B actually toggle as
// the shaft rotates, and (b) whether the accumulated TICK count advances with
// them. This isolates a HARDWARE/electrical cause (A/B change but TICK does
// not advance -> wiring/pin/pull-up/GND/voltage issue or a channel isn't
// reaching the interrupt pins) from a COMMS cause (TICK advances in the Serial
// Monitor but the dashboard shows 0/1 -> dashboard parse issue).
//
// The normal quadrature counting path is 100% UNCHANGED: g_currentTicks is
// still accumulated ONLY by the ISR via the transition table, is never
// overwritten by a digitalRead, and is never reset in loop(). This flag only
// ADDS diagnostic output; it cannot make the count read 0/1.
//
// NOTE: the default is guarded with #ifndef so a command-line
// -DENCODER_RAW_DEBUG=1 (or -D...) takes precedence over this source default.
// Compile the diagnostic build with:
//   arduino-cli compile --fqbn arduino:avr:mega \
//       --build-property compiler.cpp.extra_flags=-DENCODER_RAW_DEBUG=1
// (Without that flag the default 0 applies and NO debug output is emitted.)
#ifndef ENCODER_RAW_DEBUG
#define ENCODER_RAW_DEBUG                0
#endif

// ── Timing ────────────────────────────────────────────────────
#define WATCHDOG_TIMEOUT_MS   2000UL
#define DIRECTION_TIMEOUT_MS  6000UL
#define DATA_PERIOD_MS        100UL
#define PERSIST_PERIOD_MS     1000UL
// How long the encoder must be quiet (no edges) before a pending counter
// EEPROM persist is allowed. This keeps EEPROM programming (which briefly
// disables interrupts ~3ms/byte on AVR) out of active motion, so the live
// counter never drops edges — ZERO MISSED COUNTS is the hard constraint.
// Trade-off: a power loss in the middle of a block move loses the exact live
// counter (the block is re-zeroed at a reference anyway); at rest it recovers.
#define PERSIST_QUIESCENCE_MS 250UL
#define SERIAL_BAUD           9600
#define SERIAL_LINE_MAX       220
#define OUTBUF_SIZE           640

// ── Velocity ──────────────────────────────────────────────────
// EMA smoothing factor (1.0 = no smoothing).
#define VELOCITY_SMOOTH       0.5f
// Below this |velocity| the direction reports STOPPED.
#define VELOCITY_STOP_EPS     0.01f
// A stopped block within this many feet of the LOWEST calibrated anchor
// is reported as direction "ON BOTTOM".
#define ON_BOTTOM_EPS_FT      0.75f


// ============================================================
//  SECTION 4: CALIBRATION CONFIGURATION
// ============================================================

// One operator row == one layer == one calibration point: (counter, feet).
#define MAX_CAL_POINTS                   4
#define MIN_CAL_POINTS                   2
#define DEFAULT_WITS_CORRECTION          0.0f
#define MIN_POSITION_FEET                -100000.0f
#define MAX_POSITION_FEET                1000000.0f
#define MIN_COUNTS_PER_FOOT              0.001f
#define MAX_COUNTS_PER_FOOT              1000000.0f

// Encoder direction polarity: +1 = counter increasing means UP (default);
// -1 = counter decreasing means UP (reversed installation).
#define DEFAULT_ENCODER_POLARITY         1


// ============================================================
//  SECTION 5: EEPROM MAP
// ============================================================

// Calibration block (v4.1):
//   0   uint16 CAL_MAGIC
//   2   float  witsCorrectionFt
//   6   long   calCounter[4]       (6..21)
//  22   float  calPosition[4]      (22..37)
//  38   uint8  calUsedMask
//  39   int8   encoderPolarity
//  40   uint16 Fletcher-16 over bytes 2..39
// Counter block:
//  42   uint16 COUNTER_MAGIC
//  44   long   counter (currentTicks)
//  48   uint16 Fletcher-16 over bytes 44..47
#define CAL_MAGIC    0x5C41
#define EEPROM_ADDR_CAL_MAGIC           0
#define EEPROM_ADDR_WITS                2
#define EEPROM_ADDR_CAL_CTR             6
#define EEPROM_ADDR_CAL_POS             22
#define EEPROM_ADDR_CAL_USED            38
#define EEPROM_ADDR_CAL_POLARITY        39
#define EEPROM_ADDR_CAL_FLET            40
#define CAL_BLOCK_LEN                   38

#define COUNTER_MAGIC    0x5C4E
#define EEPROM_ADDR_CTR_MAGIC           42
#define EEPROM_ADDR_CTR_COUNTER         44
#define EEPROM_ADDR_CTR_FLET            48
#define CTR_BLOCK_LEN                   4

#define EEPROM_SAFE \
  (EEPROM_ADDR_CTR_FLET + 2)


// ============================================================
//  SECTION 6: ERROR CODES AND TYPE DEFINITIONS
// ============================================================

// Serial protocol error codes.
typedef enum {
  CMD_OK                =  0,
  CMD_ERR_UNKNOWN       =  1,
  CMD_ERR_PARAM         =  2,
  CMD_ERR_VALUE         =  3,
  CMD_ERR_OUT_OF_RANGE  =  4,
  CMD_ERR_NO_PENDING    =  7,
  CMD_ERR_OUT_OF_MEMORY =  8,
  CMD_ERR_CAL_DUP_PULSES = 9,
  CMD_ERR_CAL_DUP_POSITION = 10,
  CMD_ERR_CAL_ZERO_INTERVAL = 11,
  CMD_ERR_CAL_BAD_ORDER = 12,
  CMD_ERR_CAL_FEW_POINTS = 13,
  CMD_ERR_CAL_BAD_POLARITY = 14,
  CMD_ERR_CAL_ZERO_POSITION = 15
} CmdError;

// Calibration-validation outcome (rejects ambiguous/non-monotonic anchors).
typedef enum {
  CAL_OK = 0,
  CAL_VAL_DUP_PULSES,
  CAL_VAL_DUP_POSITION,
  CAL_VAL_ZERO_INTERVAL,
  CAL_VAL_BAD_ORDER,
  CAL_VAL_FEW_POINTS,
  CAL_VAL_BAD_POLARITY,
  CAL_VAL_ZERO_POSITION
} CalValidation;

// Calibration evaluation result for one counter sample.
typedef enum {
  CAL_NO_CALIBRATION = 0,
  CAL_VALID,
  CAL_OUT_LOW,
  CAL_OUT_HIGH
} CalStatus;

typedef struct {
  bool      valid;
  bool      inRange;
  CalStatus status;
  float     calibratedPosition;
  float     reportedPositionFt;
  uint8_t   interval;
} CalPointResult;

// Sorted calibration point for internal use.
typedef struct {
    long  counter;
    float pos;
} CalPt;


// ============================================================
//  SECTION 7: GLOBALS AND STATIC VARIABLES
// ============================================================

// ── Interrupt-owned state ─────────────────────────────────────
volatile uint8_t g_quad_state[4];
volatile long    g_currentTicks;
volatile unsigned long g_illegal_count;
volatile char    g_lastActivity;
volatile unsigned long g_lastActivityMs;

// ── Calibration table (not touched by ISRs) ──────────────────
long    g_calCounter[MAX_CAL_POINTS];
float   g_calPosition[MAX_CAL_POINTS];
uint8_t g_calUsedMask = 0x00;
float   g_witsCorrectionFt = DEFAULT_WITS_CORRECTION;
// Volatile: read inside the quadrature ISR (encoder_isr).
static volatile int8_t g_encoderPolarity = DEFAULT_ENCODER_POLARITY;

// ── Boot bookkeeping ─────────────────────────────────────────
unsigned long g_uptime_s;
const char* g_boot_reason = "POWER_ON";
const char* g_boot_error = "";
uint32_t g_sequence = 0;

// ── Reset request flag ───────────────────────────────────────
volatile bool g_reset_requested = false;

// ── Velocity bookkeeping (loop()/protocol domain) ────────────
static unsigned long s_vel_last_ms = 0;
static float         s_vel_last_pos = 0.0f;
static bool          s_vel_initialized = false;
static float         g_velocity_ft_min = 0.0f;

// ── RESET FEET runtime zero reference ─────────────────────────
// RESET FEET re-establishes a TEMPORARY runtime zero independent of the saved
// calibration anchors: the block position at the reset instant reads 0.00 ft
// and the reading tracks movement RELATIVE to that point until a RESET
// COUNTER / re-calibration / clear clears the reference. This must NOT touch
// the calibration table. To keep position continuous across the reset (ticks
// are zeroed but calibration is NOT re-based), we record the pre-reset tick
// count and pre-reset reported position, then offset BOTH the effective ticks
// used for lookups and the displayed position:
//     effectiveTicks = liveCounter + g_runtime_tick_offset
//     displayPos     = positionAt(effectiveTicks).reported - g_runtime_pos_offset
static long   g_runtime_tick_offset   = 0L;
static float  g_runtime_pos_offset    = 0.0f;
static bool   g_runtime_offset_active = false;
// Last counter value actually persisted to EEPROM (change-guard for saving).
static volatile long g_counter_last_persisted = 0L;

// ── Quadrature transition matrix ─────────────────────────────
// Each state is (A << 1) | B. s_translate[old][new] returns ±1 for legal
// single-line moves, 0 for impossible diagonal, and ILLEGAL transitions
// are discarded and counted.
static const char s_translate[4][4] = {
  {  0, -1,  1,  0 },
  {  1,  0,  0, -1 },
  { -1,  0,  0,  1 },
  {  0,  1, -1,  0 },
};

// Sentinel used to seed the quadrature decoder before any edge is seen.
#ifndef QSTATE_INVALID
#define QSTATE_INVALID 0xFF
#endif

// Last observed encoder state (seeded at boot so first real edge counts).
static volatile uint8_t s_last_state = QSTATE_INVALID;

// ── Serial protocol inbound buffer ───────────────────────────
static char      g_in[SERIAL_LINE_MAX];
static uint16_t  g_in_len = 0;

// ── Serial protocol outbound buffer ──────────────────────────
static char    g_out[OUTBUF_SIZE];
static char*   g_w;
static uint8_t g_s1, g_s2;

// ── Staged command pending state ─────────────────────────────
typedef enum {
    STAGE_NONE,
    STAGE_SET_CAL_POINT,
    STAGE_SET_WITS,
    STAGE_CLEAR_CAL_POINT,
    STAGE_SET_POLARITY,
    STAGE_RESTORE_DEFAULTS
} PendingKind;

static struct {
    bool    active;
    PendingKind kind;
    float   value;
    long    counter;
    uint8_t layer;
    bool    has_counter;
    long    req_id;
} g_pending = { false, STAGE_NONE, 0.0f, 0, 0, false, -1 };

// ── JSON scanner field type ──────────────────────────────────
typedef struct { char key[24]; char type; char sval[40]; float fval; long lval; } JField;

// ── Reset button debounce state ──────────────────────────────
static int         reset_btn_state    = HIGH;
static unsigned long reset_btn_millis = 0UL;


// ============================================================
//  SECTION 8: FORWARD DECLARATIONS
// ============================================================
static void cal_build_sorted(CalPt out[MAX_CAL_POINTS], uint8_t* used);
static void IRAM_ATTR encoder_isr(void);
static void ob_open(void);
static void ob_put(char c);
static void ob_str(const char* key, const char* val, bool comma);
static void ob_float(const char* key, float val, int prec, bool comma);
static void ob_long(const char* key, long val, bool comma);
static bool ob_finish(void);
static const char* cal_status_str(CalStatus s);
static void ob_append_measurement_summary(bool comma);
static void send_ack(const char* action, const char* state, const char* detail,
                     bool has_echo, float echo, CmdError ec, long req_id, bool has_req);
static void send_status_snapshot(long req_id, bool has_req);
static void send_report(void);
static bool send_param_error(const char* action, CmdError ec, const char* detail);
static void stage(PendingKind kind, float value, long req_id);
static CmdError cal_validation_code(CalValidation v);
static const char* cal_validation_detail(CalValidation v);
static void apply_pending(void);
static bool valid_position(float v);
static void handle_line(char* line);
static int scan_json(char* line, JField* f, int maxf);
static bool eeprom_cal_is_valid(void);
static void eeprom_cal_save(void);
static bool eeprom_cal_restore(void);
static void eeprom_cal_clear(void);
static bool eeprom_counter_is_valid(void);
static void eeprom_counter_save(void);
static bool eeprom_counter_restore(void);
static void eeprom_counter_clear(void);
void encoder_begin(void);
void encoder_attach_interrupts(void);
void encoder_process(void);
void encoder_on_change(void);
long encoder_read_counter(void);
void encoder_set_counter(long value);
void encoder_execute_reset(void);
void encoder_execute_reset_feet(void);
CalPointResult encoder_position_at(long ticks);
CalPointResult encoder_current_position(void);
long encoder_effective_ticks(void);
float encoder_display_position_ft(void);
float encoder_velocity_ft_min(void);
char encoder_direction(void);
bool encoder_on_bottom(void);
uint8_t encoder_current_layer(long liveCounter);
long encoder_cal_counter(uint8_t i);
float encoder_cal_position(uint8_t i);
float encoder_cal_counts_per_foot(uint8_t i);
uint8_t encoder_cal_used(void);
bool encoder_cal_configured(uint8_t i);
void encoder_set_calibration_point(uint8_t i, long counter, float positionFt);
void encoder_clear_calibration_point(uint8_t i);
CalValidation encoder_validate_calibration_point(uint8_t i, long counter, float positionFt);
float encoder_wits_correction(void);
void encoder_set_wits_correction(float v);
int8_t encoder_polarity(void);
bool encoder_set_polarity(int8_t p);
void encoder_set_calibration_defaults(void);
unsigned long encoder_illegal_transitions(void);
void encoder_clear_illegal_transitions(void);
void protocol_init(void);
void protocol_process(void);
void protocol_event_reset(const char* source);
void protocol_event_reset_feet(const char* source);
void protocol_send_log(const char* message);

// QSTATE_INVALID must be defined before the forward declarations use it.
#ifndef QSTATE_INVALID
#define QSTATE_INVALID 0xFF
#endif


// ============================================================
//  SECTION 9: ENCODER ISRs
// ============================================================

static void IRAM_ATTR encoder_isr(void)
{
    uint8_t newState = (digitalRead(PIN_ENCODER_A) ? 1 : 0) << 1
                     | (digitalRead(PIN_ENCODER_B) ? 1 : 0);

    if (newState == s_last_state) return;

    if (s_last_state == QSTATE_INVALID) {
        s_last_state = newState;
        g_quad_state[newState] = 1;
        return;
    }

    int8_t step = s_translate[s_last_state][newState];
    if (step) {
        g_currentTicks += (g_encoderPolarity > 0) ? step : -step;
        g_lastActivity = (step > 0) ? 'u' : 'd';
        g_lastActivityMs = millis();
    } else {
        g_illegal_count++;
    }
    s_last_state = newState;
    g_quad_state[newState] = 1;
}

void IRAM_ATTR encoder_on_change(void)
{
    encoder_isr();
}


// ============================================================
//  SECTION 10: ENCODER FUNCTIONS (init, counter, lifecycle)
// ============================================================

void encoder_begin(void)
{
    memset((void*)g_quad_state, QSTATE_INVALID, sizeof(g_quad_state));
    memset((void*)g_calCounter, 0, sizeof(g_calCounter));
    memset((void*)g_calPosition, 0, sizeof(g_calPosition));
    g_calUsedMask = 0x00;
    g_witsCorrectionFt = DEFAULT_WITS_CORRECTION;
    g_encoderPolarity   = DEFAULT_ENCODER_POLARITY;
    g_currentTicks  = 0;
    g_illegal_count = 0;
    g_lastActivity  = ' ';
    g_lastActivityMs = 0;
    g_uptime_s       = 0;
    g_velocity_ft_min = 0.0f;
    s_vel_initialized = false;

    pinMode(PIN_ENCODER_A, INPUT_PULLUP);
    pinMode(PIN_ENCODER_B, INPUT_PULLUP);
    s_last_state = (digitalRead(PIN_ENCODER_A) ? 1 : 0) << 1
                 | (digitalRead(PIN_ENCODER_B) ? 1 : 0);
    if (s_last_state < 4) g_quad_state[s_last_state] = 1;
}

void encoder_attach_interrupts(void)
{
    pinMode(PIN_ENCODER_A, INPUT_PULLUP);
    pinMode(PIN_ENCODER_B, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_ENCODER_A), encoder_on_change, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_ENCODER_B), encoder_on_change, CHANGE);
}

void encoder_process(void)
{
    unsigned long now = millis();
    static unsigned long last_uptime_ms = 0;
    if (now - last_uptime_ms >= 1000UL) {
        last_uptime_ms = now;
        g_uptime_s++;
    }
}

long encoder_read_counter(void)
{
    noInterrupts();
    long c = g_currentTicks;
    interrupts();
    return c;
}

void encoder_set_counter(long value)
{
    noInterrupts();
    g_currentTicks = value;
    interrupts();
}

void encoder_execute_reset(void)
{
    // Atomic RESET COUNTER: zero the live counter while KEEPING the displayed
    // position continuous — WITHOUT touching the calibration table or EEPROM.
    //
    // Position continuity is achieved with the SAME runtime-reference mechanism
    // used by RESET FEET, but with the position offset set to 0 so the reading
    // is preserved (it does NOT jump to a calibration anchor and does NOT read
    // 0.00). The operator's calibration input (calCounterN/calPositionN) and the
    // saved EEPROM calibration stay byte-for-byte identical.
    //
    // The ISR is the only other writer of g_currentTicks, so disable interrupts
    // for the read-and-zero; a torn delta would make the runtime offset wrong.
    noInterrupts();
    long delta = g_currentTicks;
    g_currentTicks = 0;
    interrupts();
    g_runtime_tick_offset   = delta;   // keep position lookup continuous
    g_runtime_pos_offset    = 0.0f;    // preserve (do not subtract) position
    g_runtime_offset_active = true;
    s_vel_last_pos = 0.0f;
    s_vel_last_ms  = millis();
    g_velocity_ft_min = 0.0f;
    g_lastActivity  = ' ';
    g_lastActivityMs = 0;
}

void encoder_execute_reset_feet(void)
{
    // RESET FEET — runtime ONLY. This is a DIFFERENT operation from
    // encoder_execute_reset(): it zeroes the live counter and re-establishes a
    // TEMPORARY runtime zero reference (position reads 0.00 ft at the reset
    // instant and tracks movement relative to it) and NEVER touches the
    // calibration table.
    //
    // CRITICAL: RESET FEET must NOT re-base, save, clear, delete or otherwise
    // modify the calibration table. The calibration anchors (calCounterN /
    // calPositionN) and the saved EEPROM calibration stay byte-for-byte
    // unchanged. Only volatile runtime state is reset (the same guarantee
    // RESET COUNTER now also provides).
    //
    // Position continuity: ticks are zeroed but calibration is NOT re-based, so
    // the position lookup would otherwise jump to the calibration anchor at
    // tick=0. To keep the reading continuous AND make the reset instant 0.00 ft,
    // we record the pre-reset live counter and pre-reset reported position into
    // the runtime offset (see encoder_effective_ticks / encoder_display_position_ft).
    long base_t   = encoder_read_counter();
    float base_pos = encoder_position_at(base_t).reportedPositionFt;
    noInterrupts();
    g_currentTicks = 0;
    interrupts();
    g_runtime_tick_offset   = base_t;
    g_runtime_pos_offset    = base_pos;
    g_runtime_offset_active = true;
    s_vel_last_pos   = 0.0f;
    s_vel_last_ms    = millis();
    g_velocity_ft_min = 0.0f;
    g_lastActivity   = ' ';
    g_lastActivityMs = 0;
}

unsigned long encoder_illegal_transitions(void) { return g_illegal_count; }
void encoder_clear_illegal_transitions(void)     { g_illegal_count = 0; }


// ============================================================
//  SECTION 11: CALIBRATION TABLE ACCESSORS
// ============================================================

static void cal_build_sorted(CalPt out[MAX_CAL_POINTS], uint8_t* used)
{
    uint8_t k = 0;
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        if (g_calUsedMask & (1u << i)) {
            out[k].counter = g_calCounter[i];
            out[k].pos     = g_calPosition[i];
            k++;
        }
    }
    for (uint8_t i = 1; i < k; i++) {
        CalPt t = out[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && out[j].counter > t.counter) {
            out[j + 1] = out[j];
            j--;
        }
        out[j + 1] = t;
    }
    *used = k;
}

long encoder_cal_counter(uint8_t i)
{
    if (i < MAX_CAL_POINTS) return g_calCounter[i];
    return 0;
}

float encoder_cal_position(uint8_t i)
{
    if (i < MAX_CAL_POINTS) return g_calPosition[i];
    return 0.0f;
}

uint8_t encoder_cal_used(void)
{
    uint8_t n = 0;
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        if (g_calUsedMask & (1u << i)) n++;
    }
    return n;
}

bool encoder_cal_configured(uint8_t i)
{
    return (i < MAX_CAL_POINTS) && (g_calUsedMask & (1u << i));
}

float encoder_cal_counts_per_foot(uint8_t i)
{
    CalPt pts[MAX_CAL_POINTS];
    uint8_t n = 0;
    cal_build_sorted(pts, &n);
    if (n < MIN_CAL_POINTS || i + 1 >= n) return 0.0f;
    float dpos = pts[i + 1].pos - pts[i].pos;
    if (fabsf(dpos) < 1e-6f) return 0.0f;
    float cpf = (float)(pts[i + 1].counter - pts[i].counter) / dpos;
    if (cpf <= 0.0f || !isfinite(cpf)) return 0.0f;
    return cpf;
}

void encoder_set_calibration_point(uint8_t i, long counter, float positionFt)
{
    if (i < MAX_CAL_POINTS) {
        g_calCounter[i]  = counter;
        g_calPosition[i] = positionFt;
        g_calUsedMask |= (1u << i);
    }
    // A new/changed calibration anchor invalidates any active RESET FEET
    // runtime-zero reference; position returns to calibration+WITS reading.
    g_runtime_tick_offset = 0L;
    g_runtime_pos_offset  = 0.0f;
    g_runtime_offset_active = false;
}

void encoder_clear_calibration_point(uint8_t i)
{
    if (i < MAX_CAL_POINTS) {
        g_calCounter[i]  = 0;
        g_calPosition[i] = 0.0f;
        g_calUsedMask &= (uint8_t)~(1u << i);
    }
    g_runtime_tick_offset = 0L;
    g_runtime_pos_offset  = 0.0f;
    g_runtime_offset_active = false;
}

float encoder_wits_correction(void) { return g_witsCorrectionFt; }
void  encoder_set_wits_correction(float v) { g_witsCorrectionFt = v; }

int8_t encoder_polarity(void) { return g_encoderPolarity; }
bool encoder_set_polarity(int8_t p)
{
    if (p != 1 && p != -1) return false;
    g_encoderPolarity = p;
    s_vel_initialized = false;
    return true;
}

CalValidation encoder_validate_calibration_point(uint8_t i, long counter, float positionFt)
{
    if (positionFt == 0.0f) {
        return CAL_VAL_ZERO_POSITION;
    }
    for (uint8_t j = 0; j < MAX_CAL_POINTS; j++) {
        if (j == i || !(g_calUsedMask & (1u << j))) continue;
        if (g_calCounter[j] == counter) {
            return CAL_VAL_DUP_PULSES;
        }
        if (fabsf(g_calPosition[j] - positionFt) < 1e-6f) {
            return CAL_VAL_DUP_POSITION;
        }
        bool cAbove = (g_calCounter[j] > counter);
        bool pAbove = (g_calPosition[j] > positionFt);
        if (cAbove != pAbove) return CAL_VAL_BAD_ORDER;
    }
    return CAL_OK;
}

void encoder_set_calibration_defaults(void)
{
    memset((void*)g_calCounter, 0, sizeof(g_calCounter));
    memset((void*)g_calPosition, 0, sizeof(g_calPosition));
    g_calUsedMask = 0x00;
    g_witsCorrectionFt = DEFAULT_WITS_CORRECTION;
    g_encoderPolarity   = DEFAULT_ENCODER_POLARITY;
    s_vel_initialized = false;
    g_runtime_tick_offset = 0L;
    g_runtime_pos_offset  = 0.0f;
    g_runtime_offset_active = false;
}


// ============================================================
//  SECTION 12: POSITION CALCULATION
// ============================================================

CalPointResult encoder_position_at(long ticks)
{
    CalPointResult r;
    memset(&r, 0, sizeof(r));
    r.status = CAL_NO_CALIBRATION;
    r.interval = 0xFF;

    CalPt pts[MAX_CAL_POINTS];
    uint8_t n = 0;
    cal_build_sorted(pts, &n);
    if (n == 0) return r;

    r.valid = true;
    r.calibratedPosition = pts[0].pos;
    r.interval = 0;

    if (n == 1) {
        if (ticks == pts[0].counter) {
            r.status = CAL_VALID;
            r.inRange = true;
        } else if (ticks < pts[0].counter) {
            r.status = CAL_OUT_LOW;
        } else {
            r.status = CAL_OUT_HIGH;
        }
        r.calibratedPosition = pts[0].pos;
        r.reportedPositionFt = r.calibratedPosition + g_witsCorrectionFt;
        return r;
    }

    uint8_t lo = 0;
    if (ticks < pts[0].counter) {
        r.status = CAL_OUT_LOW;
        lo = 0;
    } else if (ticks > pts[n - 1].counter) {
        r.status = CAL_OUT_HIGH;
        lo = n - 2;
    } else {
        for (uint8_t i = 0; i + 1 < n; i++) {
            if (ticks >= pts[i].counter && ticks <= pts[i + 1].counter) {
                lo = i;
                r.status = CAL_VALID;
                r.inRange = true;
                break;
            }
        }
    }
    r.interval = lo;

    {
        long   c1 = pts[lo].counter, c2 = pts[lo + 1].counter;
        float  p1 = pts[lo].pos,     p2 = pts[lo + 1].pos;
        if (c2 == c1) {
            r.calibratedPosition = p1;
        } else {
            float frac = (float)(ticks - c1) / (float)(c2 - c1);
            r.calibratedPosition = p1 + frac * (p2 - p1);
        }
    }

    r.reportedPositionFt = r.calibratedPosition + g_witsCorrectionFt;
    return r;
}

CalPointResult encoder_current_position(void)
{
    return encoder_position_at(encoder_read_counter());
}

// Effective tick used for position lookups / layer when a RESET FEET runtime
// offset is active. Position must stay continuous across the tick-zeroing, so
// the recording of where the physical counter was is added back for lookups.
long encoder_effective_ticks(void)
{
    return encoder_read_counter()
         + (g_runtime_offset_active ? g_runtime_tick_offset : 0L);
}

// Displayed position: calibration+WITS position at the effective ticks, with
// the RESET FEET reference subtracted so the reset instant reads 0.00 ft.
float encoder_display_position_ft(void)
{
    CalPointResult r = encoder_position_at(encoder_effective_ticks());
    if (g_runtime_offset_active) return r.reportedPositionFt - g_runtime_pos_offset;
    return r.reportedPositionFt;
}


// ============================================================
//  SECTION 13: VELOCITY CALCULATION
// ============================================================

float encoder_velocity_ft_min(void)
{
    unsigned long now = millis();
    if (!s_vel_initialized) {
        s_vel_last_pos  = encoder_current_position().calibratedPosition;
        s_vel_last_ms   = now;
        s_vel_initialized = true;
        return 0.0f;
    }
    unsigned long dt_ms = now - s_vel_last_ms;
    if (dt_ms == 0) return g_velocity_ft_min;
    if (dt_ms < (DATA_PERIOD_MS + DATA_PERIOD_MS) / 4) return g_velocity_ft_min;
    float pos_now = encoder_current_position().calibratedPosition;
    float inst = (pos_now - s_vel_last_pos) * 60000.0f / (float)dt_ms;
    s_vel_last_pos = pos_now;
    s_vel_last_ms  = now;
    g_velocity_ft_min = (VELOCITY_SMOOTH * inst)
                      + ((1.0f - VELOCITY_SMOOTH) * g_velocity_ft_min);
    if (fabsf(g_velocity_ft_min) < VELOCITY_STOP_EPS) g_velocity_ft_min = 0.0f;
    return g_velocity_ft_min;
}

char encoder_direction(void)
{
    char a = g_lastActivity;
    if (a != 'u' && a != 'd') return 'S';
    if ((millis() - g_lastActivityMs) > DIRECTION_TIMEOUT_MS) return 'S';
    if (fabsf(g_velocity_ft_min) < VELOCITY_STOP_EPS) return 'S';
    return (a == 'u') ? 'U' : 'D';
}

bool encoder_on_bottom(void)
{
    if (encoder_direction() != 'S') return false;
    // When RESET FEET has established a runtime zero reference, "on bottom"
    // is judged against that referenced 0.00 ft position (not the calibration
    // anchor), so the displayed position and on-bottom flag agree.
    if (g_runtime_offset_active) {
        return encoder_display_position_ft() <= ON_BOTTOM_EPS_FT;
    }
    CalPointResult r = encoder_current_position();
    if (!r.valid) return false;
    CalPt pts[MAX_CAL_POINTS];
    uint8_t n = 0;
    cal_build_sorted(pts, &n);
    if (n == 0) return false;
    return r.calibratedPosition <= (pts[0].pos + ON_BOTTOM_EPS_FT);
}

uint8_t encoder_current_layer(long liveCounter)
{
    uint8_t layer = 1;
    long best = liveCounter + 1;
    int8_t best_idx = -1;
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        if (g_calUsedMask & (1u << i)) {
            long c = g_calCounter[i];
            if (c <= liveCounter && c < best) {
                best = c;
                best_idx = (int8_t)i;
            }
        }
    }
    if (best_idx >= 0) layer = (uint8_t)best_idx + 1;
    return layer;
}


// ============================================================
//  SECTION 14: SERIAL COMMUNICATION (outbound JSON builder)
// ============================================================

static void ob_open(void)
{
    g_w = g_out;
    *g_w++ = '{';
    g_s1 = 0;
    g_s2 = 0;
}

static void ob_put(char c)
{
    if ((size_t)(g_w - g_out) < OUTBUF_SIZE - 1) {
        *g_w++ = c;
        g_s1 = (uint8_t)((g_s1 + (uint8_t)c) % 255);
        g_s2 = (uint8_t)((g_s2 + g_s1) % 255);
    }
}

static void ob_str(const char* key, const char* val, bool comma)
{
    if (comma) ob_put(',');
    ob_put('"');
    while (*key) { ob_put(*key); key++; }
    ob_put('"'); ob_put(':');
    ob_put('"');
    while (*val) { ob_put(*val); val++; }
    ob_put('"');
}

static void ob_float(const char* key, float val, int prec, bool comma)
{
    char b[16];
    dtostrf(val, 1, prec, b);
    if (comma) ob_put(',');
    ob_put('"');
    while (*key) { ob_put(*key); key++; }
    ob_put('"'); ob_put(':');
    char* p = b;
    while (*p) { ob_put(*p); p++; }
}

static void ob_long(const char* key, long val, bool comma)
{
    char b[14];
    ltoa(val, b, 10);
    if (comma) ob_put(',');
    ob_put('"');
    while (*key) { ob_put(*key); key++; }
    ob_put('"'); ob_put(':');
    char* p = b;
    while (*p) { ob_put(*p); p++; }
}

static bool ob_finish(void)
{
    size_t used = (size_t)(g_w - g_out);
    if (used + 16 >= OUTBUF_SIZE) {
        Serial.println(F("{\"type\":\"log\",\"message\":\"internal: out buffer full\",\"crc\":41048}"));
        return false;
    }
    const char tail[] = ",\"crc\":";
    memcpy(g_w, tail, sizeof(tail) - 1);
    g_w += sizeof(tail) - 1;
    char num[14];
    uint16_t crc = (uint16_t)(((uint16_t)g_s2 << 8) | g_s1);
    ltoa((long)crc, num, 10);
    char* p = num;
    while (*p) *g_w++ = *p++;
    *g_w++ = '}';
    *g_w = '\0';
    Serial.println(g_out);
    return true;
}


// ============================================================
//  SECTION 15: DASHBOARD COMMUNICATION (report/ack/status)
// ============================================================

static const char* cal_status_str(CalStatus s)
{
    switch (s) {
        case CAL_VALID:         return "VALID";
        case CAL_OUT_LOW:       return "OUT_OF_RANGE";
        case CAL_OUT_HIGH:      return "OUT_OF_RANGE";
        default:                return "NO_CALIBRATION";
    }
}

static void ob_append_measurement_summary(bool comma)
{
    CalPointResult r = encoder_position_at(encoder_effective_ticks());
    ob_long("currentTicks", encoder_read_counter(), comma);
    ob_float("blockPositionFt", encoder_display_position_ft(), 2, true);
    ob_float("velocityFtMin", encoder_velocity_ft_min(), 1, true);
    ob_str("direction", (encoder_direction() == 'U') ? "UP"
                        : (encoder_direction() == 'D') ? "DOWN" : "STOPPED", true);
    ob_str("calStatus", cal_status_str(r.status), true);
    ob_long("calInRange", (long)(r.inRange ? 1 : 0), true);
    ob_str("onBottom", encoder_on_bottom() ? "true" : "false", true);
    ob_long("currentLayer", (long)encoder_current_layer(encoder_effective_ticks()), true);
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        char key[16];
        if (encoder_cal_configured(i)) {
            key[0]='c'; key[1]='a'; key[2]='l'; key[3]='C'; key[4]='o'; key[5]='u';
            key[6]='n'; key[7]='t'; key[8]='e'; key[9]='r'; key[10]=(char)('1'+i);
            key[11]='\0';
            ob_long(key, encoder_cal_counter(i), true);
            key[0]='c'; key[1]='a'; key[2]='l'; key[3]='P'; key[4]='o'; key[5]='s';
            key[6]='i'; key[7]='t'; key[8]='i'; key[9]='o'; key[10]='n'; key[11]=(char)('1'+i); key[12]='\0';
            ob_float(key, encoder_cal_position(i), 2, true);
        } else {
            key[0]='c'; key[1]='a'; key[2]='l'; key[3]='C'; key[4]='o'; key[5]='u';
            key[6]='n'; key[7]='t'; key[8]='e'; key[9]='r'; key[10]=(char)('1'+i);
            key[11]='\0';
            ob_long(key, 0L, true);
            key[0]='c'; key[1]='a'; key[2]='l'; key[3]='P'; key[4]='o'; key[5]='s';
            key[6]='i'; key[7]='t'; key[8]='i'; key[9]='o'; key[10]='n'; key[11]=(char)('1'+i); key[12]='\0';
            ob_float(key, 0.0f, 2, true);
        }
        key[0]='c'; key[1]='o'; key[2]='u'; key[3]='n'; key[4]='t'; key[5]='s';
        key[6]='P'; key[7]='e'; key[8]='r'; key[9]='F'; key[10]='o'; key[11]='o';
        key[12]='t'; key[13]=(char)('1'+i); key[14]='\0';
        ob_float(key, encoder_cal_counts_per_foot(i), 2, true);
    }
    ob_float("witsCorrectionFt", encoder_wits_correction(), 2, true);
    ob_long("encoderPolarity", (long)encoder_polarity(), true);
}

static void send_ack(const char* action, const char* state, const char* detail,
                     bool has_echo, float echo, CmdError ec, long req_id, bool has_req)
{
    ob_open();
    ob_str("type", "ack", false);
    ob_str("action", action, true);
    ob_str("state", state, true);
    if (has_echo) ob_float("value", echo, 4, true);
    ob_long("ec", (long)ec, true);
    if (has_req) ob_long("req_id", req_id, true);
    ob_str("detail", detail, true);
    ob_append_measurement_summary(true);
    ob_long("uptime_s", (long)g_uptime_s, true);
    ob_long("sequence", (long)g_sequence++, true);
    ob_finish();
}

static void send_status_snapshot(long req_id, bool has_req)
{
    ob_open();
    ob_str("type", "status", false);
    ob_str("fw_version", FW_VERSION, true);
    ob_str("build", FW_BUILD, true);
    ob_str("protocol_version", FW_PROTOCOL_VERSION, true);
    ob_str("boot_reason", g_boot_reason, true);
    ob_str("boot_error", g_boot_error, true);
    if (has_req) ob_long("req_id", req_id, true);
    ob_append_measurement_summary(true);
    ob_long("uptime_s", (long)g_uptime_s, true);
    ob_long("sequence", (long)g_sequence++, true);
    ob_finish();
}

static void send_report(void)
{
    CalPointResult r = encoder_position_at(encoder_effective_ticks());
    float vel = encoder_velocity_ft_min();
    ob_open();
    ob_str("type", "report", false);
    ob_long("currentTicks", encoder_read_counter(), true);
    ob_float("blockPositionFt", encoder_display_position_ft(), 2, true);
    ob_float("velocityFtMin", vel, 1, true);
    ob_str("direction", (encoder_direction() == 'U') ? "UP"
                        : (encoder_direction() == 'D') ? "DOWN" : "STOPPED", true);
    ob_str("onBottom", encoder_on_bottom() ? "true" : "false", true);
    ob_str("calStatus", cal_status_str(r.status), true);
    ob_long("calInRange", (long)(r.inRange ? 1 : 0), true);
    ob_long("currentLayer", (long)encoder_current_layer(encoder_effective_ticks()), true);
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        char key[16];
        if (encoder_cal_configured(i)) {
            key[0]='c'; key[1]='a'; key[2]='l'; key[3]='C'; key[4]='o'; key[5]='u';
            key[6]='n'; key[7]='t'; key[8]='e'; key[9]='r'; key[10]=(char)('1'+i); key[11]='\0';
            ob_long(key, encoder_cal_counter(i), true);
            key[0]='c'; key[1]='a'; key[2]='l'; key[3]='P'; key[4]='o'; key[5]='s';
            key[6]='i'; key[7]='t'; key[8]='i'; key[9]='o'; key[10]='n'; key[11]=(char)('1'+i); key[12]='\0';
            ob_float(key, encoder_cal_position(i), 2, true);
        }
        key[0]='c'; key[1]='o'; key[2]='u'; key[3]='n'; key[4]='t'; key[5]='s';
        key[6]='P'; key[7]='e'; key[8]='r'; key[9]='F'; key[10]='o'; key[11]='o';
        key[12]='t'; key[13]=(char)('1'+i); key[14]='\0';
        ob_float(key, encoder_cal_counts_per_foot(i), 2, true);
    }
    ob_float("witsCorrectionFt", encoder_wits_correction(), 2, true);
    ob_long("uptime_s", (long)g_uptime_s, true);
    ob_long("sequence", (long)g_sequence++, true);
    ob_finish();
}


// ============================================================
//  SECTION 16: VALIDATION, ERROR HANDLING, COMMAND DISPATCH
// ============================================================

static CmdError cal_validation_code(CalValidation v)
{
    switch (v) {
        case CAL_VAL_DUP_PULSES:    return CMD_ERR_CAL_DUP_PULSES;
        case CAL_VAL_DUP_POSITION:  return CMD_ERR_CAL_DUP_POSITION;
        case CAL_VAL_ZERO_INTERVAL: return CMD_ERR_CAL_ZERO_INTERVAL;
        case CAL_VAL_BAD_ORDER:     return CMD_ERR_CAL_BAD_ORDER;
        case CAL_VAL_FEW_POINTS:    return CMD_ERR_CAL_FEW_POINTS;
        case CAL_VAL_BAD_POLARITY:  return CMD_ERR_CAL_BAD_POLARITY;
        case CAL_VAL_ZERO_POSITION: return CMD_ERR_CAL_ZERO_POSITION;
        default:                    return CMD_ERR_VALUE;
    }
}

static const char* cal_validation_detail(CalValidation v)
{
    switch (v) {
        case CAL_VAL_DUP_PULSES:    return "calibration rejected: another level already uses that encoder counter";
        case CAL_VAL_DUP_POSITION:  return "calibration rejected: another level already uses that physical position";
        case CAL_VAL_ZERO_INTERVAL: return "calibration rejected: zero-length interval (duplicate counter)";
        case CAL_VAL_BAD_ORDER:     return "calibration rejected: encoder counter and physical position disagree on direction";
        case CAL_VAL_FEW_POINTS:    return "calibration rejected: need at least MIN_CAL_POINTS valid levels";
        case CAL_VAL_BAD_POLARITY:  return "calibration rejected: polarity must be +1 or -1";
        case CAL_VAL_ZERO_POSITION: return "calibration rejected: FEET cannot be 0.0 — an interval needs a non-zero span";
        default:                    return "calibration rejected";
    }
}

static bool send_param_error(const char* action, CmdError ec, const char* detail)
{
    send_ack(action ? action : "command", "error", detail, false, 0.0f,
             ec, -1, false);
    return false;
}

static void stage(PendingKind kind, float value, long req_id)
{
    g_pending.active = true;
    g_pending.kind   = kind;
    g_pending.value  = value;
    g_pending.layer  = 0;
    g_pending.counter = 0;
    g_pending.has_counter = false;
    g_pending.req_id = req_id;
}

static bool valid_position(float v)
{
    return isfinite(v) && v >= MIN_POSITION_FEET && v <= MAX_POSITION_FEET;
}

static void apply_pending(void)
{
    if (!g_pending.active) {
        send_ack("confirm", "error",
                 "no pending configuration change to confirm",
                 false, 0.0f, CMD_ERR_NO_PENDING, -1, false);
        return;
    }
    bool ok = true;
    float echo = g_pending.value;
    const char* detail = "applied (echo of active value)";
    PendingKind kind = g_pending.kind;
    switch (kind) {
        case STAGE_SET_CAL_POINT: {
            long counter = g_pending.has_counter ? g_pending.counter
                                                 : encoder_read_counter();
            CalValidation v = encoder_validate_calibration_point(
                g_pending.layer - 1, counter, g_pending.value);
            if (v != CAL_OK) {
                send_ack("set_calibration_point", "rejected",
                         cal_validation_detail(v), false, 0.0f,
                         cal_validation_code(v), g_pending.req_id, (g_pending.req_id >= 0));
                g_pending.active = false;
                return;
            }
            encoder_set_calibration_point(g_pending.layer - 1, counter, g_pending.value);
            eeprom_cal_save();
            detail = "calibration point applied (calibration saved)";
            break;
        }
        case STAGE_CLEAR_CAL_POINT:
            encoder_clear_calibration_point(g_pending.layer - 1);
            eeprom_cal_save();
            detail = "calibration level cleared (calibration saved); counter preserved";
            break;
        case STAGE_SET_POLARITY:
            if (!encoder_set_polarity((int8_t)g_pending.value)) {
                send_ack("set_encoder_polarity", "rejected",
                         "polarity must be +1 or -1", false, 0.0f,
                         CMD_ERR_CAL_BAD_POLARITY, g_pending.req_id, (g_pending.req_id >= 0));
                g_pending.active = false;
                return;
            }
            eeprom_cal_save();
            detail = "encoder direction polarity applied (persisted)";
            break;
        case STAGE_SET_WITS:
            encoder_set_wits_correction(g_pending.value);
            eeprom_cal_save();
            detail = "WITS correction applied (calibration saved)";
            break;
        case STAGE_RESTORE_DEFAULTS:
            encoder_set_calibration_defaults();
            eeprom_cal_save();
            echo = 0.0f;
            detail = "defaults applied: calibration cleared + WITS reset (counter preserved)";
            break;
        default:
            ok = false;
            detail = "internal: bad pending stage";
            break;
    }
    long req = g_pending.req_id;
    bool has_req = (req >= 0);
    g_pending.active = false;

    if (ok) {
        const char* action = (kind == STAGE_SET_CAL_POINT) ? "set_calibration_point"
                          : (kind == STAGE_CLEAR_CAL_POINT) ? "clear_calibration_point"
                          : (kind == STAGE_SET_POLARITY) ? "set_encoder_polarity"
                          : (kind == STAGE_SET_WITS) ? "set_wits_correction"
                          : "restore_defaults";
        send_ack("confirm", "done", detail,
                 kind != STAGE_RESTORE_DEFAULTS, echo, CMD_OK, req, has_req);
        protocol_send_log("CONFIG_CHANGE applied by operator (calibration saved)");
    } else {
        send_ack("confirm", "error", detail, false, 0.0f,
                 CMD_ERR_UNKNOWN, req, has_req);
    }
}

static void handle_line(char* line)
{
    JField f[10];
    int nf = scan_json(line, f, 10);
    const char* cmd = NULL;
    bool has_value = false;
    float value = 0.0f;
    long req_id = -1;
    bool has_req = false;
    long layer = 0;
    bool has_counter = false;
    long counter = 0;

    for (int i = 0; i < nf; i++) {
        if (strcmp(f[i].key, "cmd") == 0 && f[i].type == 'S') cmd = f[i].sval;
        else if (strcmp(f[i].key, "value") == 0 && (f[i].type == 'I' || f[i].type == 'F')) {
            value = f[i].fval;
            has_value = true;
        } else if (strcmp(f[i].key, "req_id") == 0 && f[i].type == 'I') {
            req_id = f[i].lval;
            has_req = true;
        } else if (strcmp(f[i].key, "layer") == 0 && f[i].type == 'I') {
            layer = f[i].lval;
        } else if (strcmp(f[i].key, "counter") == 0 && (f[i].type == 'I' || f[i].type == 'F')) {
            counter = f[i].lval;
            has_counter = true;
        }
    }

    if (!cmd) {
        send_param_error(NULL, CMD_ERR_PARAM, "JSON missing the cmd field");
        return;
    }

    if (strcmp(cmd, "status") == 0) {
        send_status_snapshot(req_id, has_req);
        return;
    }

    if (strcmp(cmd, "reset_counter") == 0) {
        protocol_event_reset("COMMAND");
        return;
    }

    if (strcmp(cmd, "reset_feet") == 0) {
        protocol_event_reset_feet("COMMAND");
        return;
    }

    if (strcmp(cmd, "confirm") == 0) {
        apply_pending();
        return;
    }

    if (strcmp(cmd, "save_calibration") == 0) {
        eeprom_cal_save();
        send_ack("save_calibration", "done", "calibration saved to EEPROM",
                 false, 0.0f, CMD_OK, req_id, has_req);
        protocol_send_log("CALIBRATION saved to EEPROM by operator");
        return;
    }

    if (strcmp(cmd, "load_calibration") == 0) {
        bool ok = eeprom_cal_restore();
        // Restored calibration supersedes any active RESET FEET runtime-zero
        // reference; position returns to the (restored) calibration+WITS reading.
        g_runtime_tick_offset = 0L;
        g_runtime_pos_offset  = 0.0f;
        g_runtime_offset_active = false;
        send_ack("load_calibration", ok ? "done" : "error",
                 ok ? "calibration loaded from EEPROM"
                    : "no valid calibration stored (Table Empty)",
                 false, 0.0f, ok ? CMD_OK : CMD_ERR_NO_PENDING, req_id, has_req);
        if (ok) protocol_send_log("CALIBRATION loaded from EEPROM by operator");
        return;
    }

    if (strcmp(cmd, "restore_defaults") == 0) {
        stage(STAGE_RESTORE_DEFAULTS, 0.0f, req_id);
        send_ack("restore_defaults", "awaiting_confirm",
                 "CONFIG RESTORE DEFAULT requested — confirm to apply (clears calibration + WITS, counter preserved)",
                 false, 0.0f, CMD_OK, req_id, has_req);
        return;
    }

    if (strcmp(cmd, "clear_calibration_point") == 0) {
        if (layer < 1 || layer > MAX_CAL_POINTS) {
            send_ack(cmd, "rejected", "layer must be 1..4", false, 0.0f,
                     CMD_ERR_OUT_OF_RANGE, req_id, has_req);
            return;
        }
        stage(STAGE_CLEAR_CAL_POINT, 0.0f, req_id);
        g_pending.layer = (uint8_t)layer;
        send_ack(cmd, "awaiting_confirm",
                 "calibration level delete staged — confirm to apply (counter preserved)",
                 false, 0.0f, CMD_OK, req_id, has_req);
        return;
    }

    if (strcmp(cmd, "set_calibration_point") == 0) {
        if (layer < 1 || layer > MAX_CAL_POINTS) {
            send_ack(cmd, "rejected", "layer must be 1..4", false, 0.0f,
                     CMD_ERR_OUT_OF_RANGE, req_id, has_req);
            return;
        }
        if (!has_value) {
            send_ack(cmd, "rejected", "missing numeric value (feet)", false, 0.0f,
                     CMD_ERR_VALUE, req_id, has_req);
            return;
        }
        if (!valid_position(value)) {
            send_ack(cmd, "rejected", "position value out of accepted range", false, 0.0f,
                     CMD_ERR_OUT_OF_RANGE, req_id, has_req);
            return;
        }
        stage(STAGE_SET_CAL_POINT, value, req_id);
        g_pending.layer = (uint8_t)layer;
        g_pending.counter = counter;
        g_pending.has_counter = has_counter;
        send_ack(cmd, "awaiting_confirm",
                 "calibration point staged — confirm to apply (counter preserved)",
                 true, value, CMD_OK, req_id, has_req);
        return;
    }

    if (strcmp(cmd, "set_wits_correction") == 0) {
        if (!has_value) {
            send_ack(cmd, "rejected", "missing numeric value", false, 0.0f,
                     CMD_ERR_VALUE, req_id, has_req);
            return;
        }
        if (!valid_position(value)) {
            send_ack(cmd, "rejected", "WITS correction out of accepted range", false, 0.0f,
                     CMD_ERR_OUT_OF_RANGE, req_id, has_req);
            return;
        }
        stage(STAGE_SET_WITS, value, req_id);
        send_ack(cmd, "awaiting_confirm",
                 "WITS correction staged — confirm to apply", true, value,
                 CMD_OK, req_id, has_req);
        return;
    }

    if (strcmp(cmd, "set_encoder_polarity") == 0) {
        if (!has_value) {
            send_ack(cmd, "rejected", "missing numeric value", false, 0.0f,
                     CMD_ERR_VALUE, req_id, has_req);
            return;
        }
        if (value != 1.0f && value != -1.0f) {
            send_ack(cmd, "rejected", "polarity must be +1 or -1", false, 0.0f,
                     CMD_ERR_CAL_BAD_POLARITY, req_id, has_req);
            return;
        }
        stage(STAGE_SET_POLARITY, value, req_id);
        send_ack(cmd, "awaiting_confirm",
                 "encoder polarity staged — confirm to apply", true, value,
                 CMD_OK, req_id, has_req);
        return;
    }

    send_ack(cmd, "error", "unknown command verb", false, 0.0f,
             CMD_ERR_UNKNOWN, req_id, has_req);
}

// JSON scanner (tolerant): extracts (key,value) pairs from a `{...}` text.
static char* skip_ws(char* p)
{
    while (*p == ' ' || *p == '\t') p++;
    return p;
}

static int scan_json(char* line, JField* f, int maxf)
{
    char* p = line;
    int nf = 0;
    while (*p && *p != '{') p++;
    if (*p != '{') return 0;
    p++;
    while (*p) {
        p = skip_ws(p);
        if (*p == '}') break;
        if (*p == ',') { p++; continue; }
        if (nf >= maxf) break;
        JField* cur = &f[nf];
        char* q = skip_ws(p);
        if (*q != '"') return nf;
        q++;
        size_t n = 0;
        while (*q && *q != '"') {
            if (n + 1 < sizeof(cur->key)) cur->key[n++] = *q;
            q++;
        }
        if (*q != '"') return nf;
        cur->key[n] = '\0';
        q++;
        q = skip_ws(q);
        if (*q != ':') return nf;
        q = skip_ws(q + 1);
        if (*q == '"') {
            q++;
            size_t m = 0;
            while (*q && *q != '"') {
                if (m + 1 < sizeof(cur->sval)) cur->sval[m++] = *q;
                q++;
            }
            if (*q == '"') q++;
            cur->sval[m] = '\0';
            cur->type = 'S';
        } else {
            char* mend = NULL;
            float fv = (float)strtod(q, &mend);
            if (mend == q) { cur->type = 'N'; }
            else {
                bool dot = false;
                for (char* r = q; r < mend; r++) {
                    if (*r == '.' || *r == 'e' || *r == 'E') { dot = true; break; }
                }
                cur->type = dot ? 'F' : 'I';
                cur->fval = fv;
                cur->lval = (long)strtol(q, NULL, 10);
            }
            p = mend ? mend : q;
            nf++;
            continue;
        }
        p = q;
        nf++;
    }
    return nf;
}


// ============================================================
//  SECTION 17: EEPROM PERSISTENCE
// ============================================================

// ── Tiny helpers ─────────────────────────────────────────────
static inline long clamp_long(long v, long lo, long hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}
static inline float clamp_float(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

// ── Low-level read/write ─────────────────────────────────────
static void write_long(int addr, long v)
{
    EEPROM.write(addr,     (uint8_t)(v & 0xFF));
    EEPROM.write(addr + 1, (uint8_t)((v >> 8) & 0xFF));
    EEPROM.write(addr + 2, (uint8_t)((v >> 16) & 0xFF));
    EEPROM.write(addr + 3, (uint8_t)((v >> 24) & 0xFF));
}

static long read_long(int addr)
{
    long v = 0;
    v |= (long)EEPROM.read(addr);
    v |= (long)EEPROM.read(addr + 1) << 8;
    v |= (long)EEPROM.read(addr + 2) << 16;
    v |= (long)EEPROM.read(addr + 3) << 24;
    return v;
}

static void write_float(int addr, float f)
{
    union { float f; uint32_t u; } u;
    u.f = f;
    EEPROM.write(addr,     (uint8_t)(u.u & 0xFF));
    EEPROM.write(addr + 1, (uint8_t)((u.u >> 8) & 0xFF));
    EEPROM.write(addr + 2, (uint8_t)((u.u >> 16) & 0xFF));
    EEPROM.write(addr + 3, (uint8_t)((u.u >> 24) & 0xFF));
}

static float read_float(int addr)
{
    union { float f; uint32_t u; } u;
    u.u = (uint32_t)EEPROM.read(addr)
        | ((uint32_t)EEPROM.read(addr + 1) << 8)
        | ((uint32_t)EEPROM.read(addr + 2) << 16)
        | ((uint32_t)EEPROM.read(addr + 3) << 24);
    return u.f;
}

// ── Calibration block ────────────────────────────────────────
static bool eeprom_cal_is_valid(void)
{
    if (EEPROM.read(EEPROM_ADDR_CAL_MAGIC)     != (uint8_t)(CAL_MAGIC & 0xFF))  return false;
    if (EEPROM.read(EEPROM_ADDR_CAL_MAGIC + 1) != (uint8_t)((CAL_MAGIC >> 8) & 0xFF)) return false;

    uint8_t s0 = 0, s1 = 0;
    for (int i = 2; i <= 39; i++) {
        s0 = (uint8_t)(s0 + EEPROM.read(i));
        s1 = (uint8_t)(s1 + s0);
    }
    uint8_t want0 = EEPROM.read(EEPROM_ADDR_CAL_FLET);
    uint8_t want1 = EEPROM.read(EEPROM_ADDR_CAL_FLET + 1);
    return (s0 == want0) && (s1 == want1);
}

static void eeprom_cal_save(void)
{
    EEPROM.write(EEPROM_ADDR_CAL_MAGIC,     (uint8_t)(CAL_MAGIC & 0xFF));
    EEPROM.write(EEPROM_ADDR_CAL_MAGIC + 1, (uint8_t)((CAL_MAGIC >> 8) & 0xFF));

    write_float(EEPROM_ADDR_WITS, g_witsCorrectionFt);
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        write_long(EEPROM_ADDR_CAL_CTR + i * 4, g_calCounter[i]);
    }
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        write_float(EEPROM_ADDR_CAL_POS + i * 4, g_calPosition[i]);
    }
    EEPROM.write(EEPROM_ADDR_CAL_USED, g_calUsedMask);
    EEPROM.write(EEPROM_ADDR_CAL_POLARITY, (uint8_t)g_encoderPolarity);

    // Fletcher-16 over bytes 2..39.
    uint8_t s0 = 0, s1 = 0;
    for (int i = 2; i <= CAL_BLOCK_LEN + 1; i++) {
        uint8_t b = 0;
        switch (i) {
            case 2 ... 5:   b = (uint8_t)(((uint32_t)(*(uint32_t*)&g_witsCorrectionFt) >> ((i - 2) * 8)) & 0xFF); break;
            case 6 ... 21:  b = (uint8_t)((g_calCounter[i - 6] >> ((i - 6) % 4 * 8)) & 0xFF); break;
            case 22 ... 37: b = (uint8_t)(((uint32_t)(*(uint32_t*)&g_calPosition[i - 22]) >> ((i - 22) % 4 * 8)) & 0xFF); break;
            case 38:        b = g_calUsedMask; break;
            case 39:        b = (uint8_t)g_encoderPolarity; break;
            default:        b = 0; break;
        }
        s0 = (uint8_t)(s0 + b);
        s1 = (uint8_t)(s1 + s0);
    }
    EEPROM.write(EEPROM_ADDR_CAL_FLET,     s0);
    EEPROM.write(EEPROM_ADDR_CAL_FLET + 1, s1);
}

static bool eeprom_cal_restore(void)
{
    if (!eeprom_cal_is_valid()) return false;

    g_witsCorrectionFt = read_float(EEPROM_ADDR_WITS);
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        g_calCounter[i]  = read_long(EEPROM_ADDR_CAL_CTR + i * 4);
    }
    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        g_calPosition[i] = read_float(EEPROM_ADDR_CAL_POS + i * 4);
    }
    g_calUsedMask = EEPROM.read(EEPROM_ADDR_CAL_USED);
    int8_t pol = (int8_t)EEPROM.read(EEPROM_ADDR_CAL_POLARITY);
    g_encoderPolarity = (pol == 1 || pol == -1) ? pol : DEFAULT_ENCODER_POLARITY;

    for (uint8_t i = 0; i < MAX_CAL_POINTS; i++) {
        if (!(g_calUsedMask & (1u << i))) {
            g_calCounter[i]  = 0;
            g_calPosition[i] = 0.0f;
        }
    }
    return true;
}

static void eeprom_cal_clear(void)
{
    for (int i = 0; i <= EEPROM_ADDR_CAL_FLET + 1; i++) EEPROM.write(i, 0);
    EEPROM.write(EEPROM_ADDR_CAL_MAGIC,     (uint8_t)(CAL_MAGIC & 0xFF));
    EEPROM.write(EEPROM_ADDR_CAL_MAGIC + 1, (uint8_t)((CAL_MAGIC >> 8) & 0xFF));
    EEPROM.write(EEPROM_ADDR_CAL_USED, 0x00);
    EEPROM.write(EEPROM_ADDR_CAL_POLARITY, (uint8_t)DEFAULT_ENCODER_POLARITY);
    uint8_t s0 = 0, s1 = 0;
    for (int i = 2; i <= 39; i++) {
        s0 = (uint8_t)(s0 + EEPROM.read(i));
        s1 = (uint8_t)(s1 + s0);
    }
    EEPROM.write(EEPROM_ADDR_CAL_FLET,     s0);
    EEPROM.write(EEPROM_ADDR_CAL_FLET + 1, s1);
}

// ── Counter block ────────────────────────────────────────────
static bool eeprom_counter_is_valid(void)
{
    if (EEPROM.read(EEPROM_ADDR_CTR_MAGIC)     != (uint8_t)(COUNTER_MAGIC & 0xFF)) return false;
    if (EEPROM.read(EEPROM_ADDR_CTR_MAGIC + 1) != (uint8_t)((COUNTER_MAGIC >> 8) & 0xFF)) return false;

    uint8_t s0 = 0, s1 = 0;
    for (int i = EEPROM_ADDR_CTR_COUNTER; i < EEPROM_ADDR_CTR_COUNTER + CTR_BLOCK_LEN; i++) {
        s0 = (uint8_t)(s0 + EEPROM.read(i));
        s1 = (uint8_t)(s1 + s0);
    }
    uint8_t want0 = EEPROM.read(EEPROM_ADDR_CTR_FLET);
    uint8_t want1 = EEPROM.read(EEPROM_ADDR_CTR_FLET + 1);
    return (s0 == want0) && (s1 == want1);
}

static void eeprom_counter_save(void)
{
    // Use EEPROM.update() (write-of-if-different) so unchanged bytes are left
    // untouched: no redundant programming, minimal wear, and no spurious
    // interrupt-latency windows when the counter is idle. Called only when the
    // counter actually changed since the last persist (see protocol_process).
    long v = encoder_read_counter();
    EEPROM.update(EEPROM_ADDR_CTR_MAGIC,     (uint8_t)(COUNTER_MAGIC & 0xFF));
    EEPROM.update(EEPROM_ADDR_CTR_MAGIC + 1, (uint8_t)((COUNTER_MAGIC >> 8) & 0xFF));
    EEPROM.update(EEPROM_ADDR_CTR_COUNTER,         (uint8_t)(v & 0xFF));
    EEPROM.update(EEPROM_ADDR_CTR_COUNTER + 1,     (uint8_t)((v >> 8) & 0xFF));
    EEPROM.update(EEPROM_ADDR_CTR_COUNTER + 2,     (uint8_t)((v >> 16) & 0xFF));
    EEPROM.update(EEPROM_ADDR_CTR_COUNTER + 3,     (uint8_t)((v >> 24) & 0xFF));

    uint8_t s0 = 0, s1 = 0;
    for (int i = EEPROM_ADDR_CTR_COUNTER; i < EEPROM_ADDR_CTR_COUNTER + CTR_BLOCK_LEN; i++) {
        uint8_t b = EEPROM.read(i);
        s0 = (uint8_t)(s0 + b);
        s1 = (uint8_t)(s1 + s0);
    }
    EEPROM.update(EEPROM_ADDR_CTR_FLET,     s0);
    EEPROM.update(EEPROM_ADDR_CTR_FLET + 1, s1);
    g_counter_last_persisted = v;
}

static bool eeprom_counter_restore(void)
{
    if (!eeprom_counter_is_valid()) return false;
    encoder_set_counter(read_long(EEPROM_ADDR_CTR_COUNTER));
    return true;
}

static void eeprom_counter_clear(void)
{
    for (int i = EEPROM_ADDR_CTR_MAGIC; i < EEPROM_ADDR_CTR_FLET + 2; i++) EEPROM.write(i, 0);
    EEPROM.write(EEPROM_ADDR_CTR_MAGIC,     (uint8_t)(COUNTER_MAGIC & 0xFF));
    EEPROM.write(EEPROM_ADDR_CTR_MAGIC + 1, (uint8_t)((COUNTER_MAGIC >> 8) & 0xFF));
    write_long(EEPROM_ADDR_CTR_COUNTER, 0);
    uint8_t s0 = 0, s1 = 0;
    for (int i = EEPROM_ADDR_CTR_COUNTER; i < EEPROM_ADDR_CTR_COUNTER + CTR_BLOCK_LEN; i++) {
        s0 = (uint8_t)(s0 + EEPROM.read(i));
        s1 = (uint8_t)(s1 + s0);
    }
    EEPROM.write(EEPROM_ADDR_CTR_FLET,     s0);
    EEPROM.write(EEPROM_ADDR_CTR_FLET + 1, s1);
}


// ============================================================
//  SECTION 18: PUBLIC PROTOCOL API
// ============================================================

void protocol_init(void)
{
    g_in_len = 0;
    g_sequence = 0;
}

void protocol_event_reset(const char* source)
{
    encoder_execute_reset();
    // NOTE: RESET COUNTER deliberately does NOT call eeprom_cal_save(). It must
    // not persist any calibration change — the calibration input (calCounterN /
    // calPositionN) stays byte-for-byte identical. Only the live counter is
    // persisted (best-effort, at rest) for power-loss recovery.
    eeprom_counter_save();
    CalPointResult r = encoder_position_at(encoder_effective_ticks());
    ob_open();
    ob_str("type", "reset_ack", false);
    ob_long("currentTicks", 0L, true);
    ob_str("source", source, true);
    ob_float("blockPositionFt", encoder_display_position_ft(), 2, true);
    ob_float("velocityFtMin", 0.0f, 1, true);
    ob_str("direction", "STOPPED", true);
    ob_str("onBottom", encoder_on_bottom() ? "true" : "false", true);
    ob_str("calStatus", cal_status_str(r.status), true);
    ob_long("calInRange", (long)(r.inRange ? 1 : 0), true);
    ob_long("uptime_s", (long)g_uptime_s, true);
    ob_long("sequence", (long)g_sequence++, true);
    ob_finish();
    char buf[48];
    buf[0] = '\0';
    strcpy(buf, "RESET ");
    strncat(buf, source, sizeof(buf) - 8);
    protocol_send_log(buf);
}

void protocol_event_reset_feet(const char* source)
{
    // RESET FEET — resets ONLY the runtime position (tick=0, 0.00 ft,
    // velocity 0, STOPPED). It deliberately does NOT re-base, save, delete,
    // or otherwise touch the calibration table or EEPROM.
    encoder_execute_reset_feet();
    // The block position reference after this reset is a NEW temporary ZERO
    // (tick=0 -> 0.00 ft), independent of the saved calibration anchors. The
    // runtime offset (set in encoder_execute_reset_feet) keeps every subsequent
    // report reading position relative to this 0.00 ft reference too, so the
    // ack and the periodic reports agree.
    CalPointResult r = encoder_position_at(encoder_effective_ticks());
    ob_open();
    ob_str("type", "reset_feet_ack", false);
    ob_long("currentTicks", 0L, true);
    ob_str("source", source, true);
    ob_float("blockPositionFt", encoder_display_position_ft(), 2, true);
    ob_float("velocityFtMin", 0.0f, 1, true);
    ob_str("direction", "STOPPED", true);
    ob_str("onBottom", encoder_on_bottom() ? "true" : "false", true);
    ob_str("calStatus", cal_status_str(r.status), true);
    ob_long("calInRange", (long)(r.inRange ? 1 : 0), true);
    ob_long("uptime_s", (long)g_uptime_s, true);
    ob_long("sequence", (long)g_sequence++, true);
    ob_finish();
    char buf[48];
    buf[0] = '\0';
    strcpy(buf, "RESET FEET ");
    strncat(buf, source, sizeof(buf) - 8);
    protocol_send_log(buf);
}

void protocol_send_log(const char* message)
{
    ob_open();
    ob_str("type", "log", false);
    ob_str("message", message, true);
    ob_long("uptime_s", (long)g_uptime_s, true);
    ob_long("sequence", (long)g_sequence++, true);
    ob_finish();
}

void protocol_process(void)
{
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (g_in_len > 0) {
                g_in[g_in_len] = '\0';
                handle_line(g_in);
                g_in_len = 0;
            }
        } else {
            if (g_in_len < SERIAL_LINE_MAX - 1) g_in[g_in_len++] = c;
        }
    }

    static uint32_t last_report = 0, last_persist = 0;
    uint32_t now = millis();
    if (now - last_report >= DATA_PERIOD_MS) {
        last_report = now;
        send_report();
    }
    // Persist the live counter only when it has actually changed AND the
    // encoder is quiet. The change-guard avoids redundant EEPROM programming
    // while idle (no wear, no interrupt-latency); the quiescence guard keeps
    // EEPROM writes (which briefly disable interrupts per byte) out of active
    // motion so no live edges are dropped (ZERO MISSED COUNTS).
    if (now - last_persist >= PERSIST_PERIOD_MS) {
        last_persist = now;
        long live = encoder_read_counter();
        if (live != g_counter_last_persisted
            && (now - g_lastActivityMs) >= PERSIST_QUIESCENCE_MS) {
            eeprom_counter_save();
        }
    }
}


// ============================================================
//  SECTION 18b: RAW ENCODER DIAGNOSTIC OUTPUT (opt-in)
// ============================================================

// Prints the RAW Channel A / Channel B digital states SEPARATELY from the
// accumulated tick count, at ~20 Hz. Compiled out entirely unless
// ENCODER_RAW_DEBUG is enabled. See the define block near the top.
//
//   E:A=1 B=0 TICK=1548
//
// Interpretation for the operator:
//   - If A/B toggle but TICK stays 0/1 ......... HARDWARE/WIRE/ELECTRICAL issue
//     (channel not reaching pins 2/3, no common GND, wrong encoder output type,
//     too-weak pull-up, or a floating second channel). Counting code is fine.
//   - If TICK advances here but the dashboard shows 0/1 ... dashboard parse issue
//     (not possible with this code: the dashboard shows currentTicks directly).
//
// This ADDS output only; it cannot alter g_currentTicks (that is accumulated
// solely by the ISR and is never reset in loop()).
void encoder_raw_debug_line(void)
{
#if ENCODER_RAW_DEBUG
    static unsigned long last_dbg = 0;
    unsigned long now = millis();
    if (now - last_dbg < 50UL) return;
    last_dbg = now;
    const int a = digitalRead(PIN_ENCODER_A) ? 1 : 0;
    const int b = digitalRead(PIN_ENCODER_B) ? 1 : 0;
    const long t = encoder_read_counter();
    char buf[40];
    const int n = snprintf(buf, sizeof(buf), "E:A=%d B=%d TICK=%ld\r\n", a, b, (long)t);
    if (n > 0) Serial.write((const uint8_t*)buf, (size_t)n);
#endif
}


// ============================================================
//  SECTION 19: setup() AND loop()
// ============================================================

void setup() {
    const uint8_t cause = MCUSR;
    MCUSR = 0;
    if (cause & (1 << WDRF)) g_boot_reason = "WATCHDOG";
    else                     g_boot_reason = "POWER_ON";

    Serial.begin(SERIAL_BAUD);
    protocol_init();

    encoder_begin();
    pinMode(PIN_RESET_BUTTON, INPUT_PULLUP);

    bool cal_ok = eeprom_cal_restore();
    if (!cal_ok) {
        encoder_set_calibration_defaults();
        eeprom_cal_save();
    }
    bool ctr_ok = eeprom_counter_restore();
    bool eeprom_ok = (cal_ok && ctr_ok);
    if (!cal_ok) g_boot_error = "calibration block defaulted";
    if (!ctr_ok && cal_ok) g_boot_error = "counter block defaulted";

    wdt_enable(WDTO_2S);

    encoder_attach_interrupts();

    protocol_send_log(eeprom_ok ? "firmware boot OK" : "firmware boot (EEPROM partial)");
}

void loop() {
    wdt_reset();
    const unsigned long now = millis();

    encoder_process();
    protocol_process();
    encoder_raw_debug_line();

    // Physical reset button (debounced, active-low).
    {
        const int level = digitalRead(PIN_RESET_BUTTON);
        if (level != reset_btn_state) {
            if ((now - reset_btn_millis) >= RESET_DEBOUNCE_MS) {
                reset_btn_state = level;
                if (reset_btn_state == LOW) protocol_event_reset("BUTTON");
            }
        } else {
            reset_btn_millis = now;
        }
    }
    (void)now;
}
