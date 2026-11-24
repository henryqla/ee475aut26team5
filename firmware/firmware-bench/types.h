/*
 * File:        types.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-24
 * Version:     0.1.5-bench
 * Change:      0.1.5-bench (training-live-inference-handoff.md, request F):
 *              live inference. New: fault codes F5-F8 (model faults: they
 *              stop inference only, owner 2026-11-24), OUT_PROB / OUT_DET and
 *              their payloads, four inference counters in HLT and heartbeats,
 *              TASK_INFERENCE.
 *              0.1.4-bench (manual-recording-overhaul-handoff.md, request A):
 *              no detection and no snapshots. Removed: evt_t and its class and
 *              basis enums, snap_row_t, snap_t, OUT_S / OUT_EVT / OUT_SNAP and
 *              the S payload, BLE_EVT, snap_drops (heartbeats and HLT) and
 *              snap_free_q. out_l_t is in every build and carries the model
 *              channels as named floats.
 *              0.1.3-bench: OUT_L and out_l_t (log builds), n_trig in evt_t.
 *              0.1.2-bench: snapshot types, OUT_SNAP, snap_drops, snap_free_q.
 *              0.1.1-bench: sample epoch, SYS_IMU_DOWN / SYS_IMU_UP,
 *              BLE_IMU_UP, imu_recoveries.
 *              0.2.0-draft: BLE. Segment payloads shared by out_q and ble_q,
 *              ble_q and time_q items, link events, F4, ble_link in the
 *              heartbeat table, named heartbeat counters, serial_drops.
 * Description: Queue item structs, fault codes, and the context struct that
 *              hands queue and task handles to every task. Tasks share nothing
 *              else.
 */

#ifndef TYPES_H
#define TYPES_H

// ============================================================================
// Includes
// ============================================================================

#include <Arduino.h>  // Fixed-width ints and FreeRTOS types on the ESP32 core

// ============================================================================
// Globals: types
// ============================================================================

// One IMU sample in chip axes. Item type of sample_q (imu_acq -> dsp).
typedef struct {
  uint32_t n;           // Sample count since boot, from 0. Data-ready samples only
  uint32_t epoch;       // 0 at boot, +1 each time the IMU is re-initialized after
                        // F1/F2. dsp restarts its filters when this changes
  int64_t  t_us;        // esp_timer_get_time() when imu_acq woke for this sample
  float    ax, ay, az;  // Acceleration, g
  float    gx, gy, gz;  // Angular rate, dps
} imu_sample_t;

// Fault codes. F1 and F2 are recoverable: imu_acq retries the IMU and the
// supervisor reports FLT until it is back. F3 and F4 lead to the one latched
// safe state until reset. F5 to F8 are model faults: inference stops until
// reset, while sampling, the L stream and BLE go on, and the supervisor adds
// an FLT line after each HLT (owner, 2026-11-24). Never the safe state.
typedef enum : uint8_t {
  FAULT_NONE      = 0,
  FAULT_IMU_INIT  = 1,  // F1: begin or WHO_AM_I failed at boot (recoverable)
  FAULT_IMU_LOST  = 2,  // F2: WHO_AM_I mismatch at runtime, or no data for IMU_STALL_MS (recoverable)
  FAULT_HEARTBEAT = 3,  // F3: a task missed heartbeats for HEARTBEAT_TIMEOUT_MS
  FAULT_BLE_INIT  = 4,  // F4: BLE stack, service, or advertising failed to start
  FAULT_MODEL_SCHEMA = 5,  // F5: model_data.h does not fit: TFLite schema version, an op
                           // the resolver lacks, the class names, or the input/output shape
  FAULT_ARENA     = 6,  // F6: AllocateTensors failed (TENSOR_ARENA_BYTES too small)
  FAULT_INVOKE    = 7,  // F7: Invoke() returned an error (boot self-test or live)
  FAULT_SELFTEST  = 8,  // F8: boot self-test outputs differ from model_data.h's by more
                        // than SELFTEST_TOL
} fault_code_t;

// One 1 s segment summary (contract section 4.1). Times are phone epoch ms
// when synced, otherwise local ms since boot (same clock as esp_timer).
typedef struct {
  int64_t  t_start_ms;  // First sample time in the window
  int64_t  t_end_ms;    // Last sample time in the window
  bool     synced;      // false: no TIME write applied yet, times are local
  uint32_t n;           // Samples in the window
  float    rms_z;       // RMS of z_hp, g
  float    peak_z;      // Largest |z_hp|, g
  float    roll_rms;    // RMS of roll rate, dps
  float    pitch_rms;   // RMS of pitch rate, dps
} seg_t;

// BLE link changes, reported by the NimBLE callbacks and printed on Serial.
typedef enum : uint8_t {
  LINK_ADVERTISING = 0,  // Advertising started after boot (value 0)
  LINK_CONNECTED,        // A phone connected (value 0)
  LINK_DISCONNECTED,     // The phone disconnected (value: NimBLE reason code)
  LINK_MTU,              // MTU negotiated (value: MTU)
  LINK_SUBSCRIBED,       // App turned DATA notifications on (value 0)
  LINK_UNSUBSCRIBED,     // App turned DATA notifications off (value 0)
  LINK_TIME,             // Valid TIME write (value: phone epoch ms)
  LINK_TIME_BAD,         // TIME write we could not parse (value: its length)
} link_event_t;

typedef struct {
  link_event_t what;
  int64_t      value;    // Meaning depends on what, see link_event_t
} link_t;

// Type tag of an out_q item. One item becomes one Serial line.
typedef enum : uint8_t {
  OUT_L = 0,            // Every sample, printed as an L line
  OUT_SEG,              // Segment line, once per second
  OUT_HLT,              // Health line, once per second
  OUT_FLT,              // Fault line, once per second in the safe state
  OUT_BLE,              // BLE link line, on each link change
  OUT_PROB,             // Inference result, about 2 per second (request F)
  OUT_DET,              // Detection (request F)
} out_tag_t;

// Classes in a PROB line: POTHOLE, SPEED_BUMP, NONE (O4). inference.cpp checks
// that model_data.h has exactly this many.
#define OUT_PROB_CLASSES  3

// Payload of an L line: one sample in raw chip axes, plus the model channels
// after the HPF and gyro bias removal, exactly as the CNN will see them.
typedef struct {
  imu_sample_t s;       // The sample, chip axes (gyro bias still in)
  float        z_hp;    // High-passed vertical acceleration, g
  float        roll;    // Roll rate, dps, bias removed
  float        pitch;   // Pitch rate, dps, bias removed
  bool         ok;      // Gyro bias ready: false during the settle time and after an
                        // IMU recovery
} out_l_t;

// Payload of a PROB line: one inference.
typedef struct {
  uint32_t n;                         // Newest sample (L line n) in the window
  float    p[OUT_PROB_CLASSES];       // Probabilities in model_data.h class order
  uint32_t lat_us;                    // Time Invoke() took, us
} out_prob_t;

// Payload of a DET line: a detection by the board's rule (O1).
typedef struct {
  uint32_t n;                         // Newest sample in the window that fired
  uint8_t  cls;                       // Class index (never NONE)
  float    conf;                      // Its probability
} out_det_t;

// Payload of an HLT line.
typedef struct {
  uint32_t t_ms;            // Time since boot, ms
  uint32_t samples;         // Data-ready samples read since boot
  uint32_t sample_q_drops;  // Samples imu_acq could not queue
  uint32_t out_q_drops;     // Lines dsp, ble_link and supervisor could not queue
  uint32_t drdy_timeouts;   // Times imu_acq waited DRDY_TIMEOUT_MS with no INT1
  uint32_t ble_q_drops;     // Segments dsp could not queue for BLE
  uint32_t ble_sent;        // Notifications sent
  uint32_t ble_fails;       // Notifications that failed or were too long
  uint32_t serial_drops;    // Lines serial_out dropped: USB TX buffer full (no host reading)
  uint32_t imu_recoveries;  // Times the IMU came back after F1/F2
  uint32_t inferences;      // Invoke() calls on live windows since boot
  uint32_t inf_skips;       // Hops skipped: the last inference still running, or its
                            // window overwritten before it was copied
  uint32_t lat_last_us;     // Last Invoke() time, us
  uint32_t lat_max_us;      // Longest Invoke() time since boot, us
} out_hlt_t;

// Payload of an FLT line.
typedef struct {
  fault_code_t code;
} out_flt_t;

// Item type of out_q (dsp, ble_link, supervisor -> serial_out).
typedef struct {
  out_tag_t tag;            // Says which union member is valid
  union {
    out_l_t   l;
    seg_t     seg;
    out_hlt_t hlt;
    out_flt_t flt;
    link_t    link;
    out_prob_t prob;
    out_det_t  det;
  };
} out_msg_t;

// Type tag of a ble_q item.
typedef enum : uint8_t {
  BLE_SEG = 0,          // From dsp: send as a seg message
  BLE_LINK,             // From the NimBLE callbacks: link changed
  BLE_IMU_DOWN,         // From supervisor, each second while faulted
  BLE_IMU_UP,           // From supervisor, once when the IMU recovers
} ble_tag_t;

// Item type of ble_q (dsp, NimBLE callbacks, supervisor -> ble_link).
typedef struct {
  ble_tag_t tag;            // Says which union member is valid
  union {
    seg_t  seg;
    link_t link;
  };
} ble_msg_t;

// Item type of time_q (TIME write callback -> dsp). Length 1, written with
// xQueueOverwrite, so dsp always gets the latest.
typedef struct {
  int64_t phone_ms;     // Phone epoch ms from the TIME write
  int64_t local_ms;     // Local ms (esp_timer) when the write arrived
} time_sync_t;

// Tasks that send heartbeats. Indexes the supervisor's last-heard table.
typedef enum : uint8_t {
  TASK_IMU_ACQ = 0,
  TASK_DSP,
  TASK_SERIAL_OUT,
  TASK_BLE_LINK,
  TASK_INFERENCE,
  TASK_COUNT,
} task_id_t;

// Kind of a sys_q item.
typedef enum : uint8_t {
  SYS_HEARTBEAT = 0,
  SYS_FAULT,            // Fatal: the sender has stopped (F3, F4)
  SYS_IMU_DOWN,         // imu_acq lost the IMU (fault = F1 or F2) and is retrying
  SYS_IMU_UP,           // imu_acq re-initialized the IMU; samples resume
} sys_kind_t;

// Item type of sys_q (every task -> supervisor). Each sender fills only the
// counters it owns; the rest stay 0.
typedef struct {
  sys_kind_t   kind;
  task_id_t    task;            // Sender
  fault_code_t fault;           // SYS_FAULT and SYS_IMU_DOWN; in inference heartbeats, its
                                // model fault (FAULT_NONE while it runs)
  uint32_t     samples;         // imu_acq
  uint32_t     drdy_timeouts;   // imu_acq
  uint32_t     sample_q_drops;  // imu_acq
  uint32_t     imu_recoveries;  // imu_acq
  uint32_t     out_q_drops;     // dsp, ble_link, inference
  uint32_t     ble_q_drops;     // dsp
  uint32_t     ble_sent;        // ble_link
  uint32_t     ble_fails;       // ble_link
  uint32_t     serial_drops;    // serial_out
  uint32_t     inferences;      // inference
  uint32_t     inf_skips;       // inference
  uint32_t     lat_last_us;     // inference
  uint32_t     lat_max_us;      // inference
} sys_msg_t;

// Queue and task handles, filled once in setup() and read-only afterwards.
// Every task gets a pointer to it as its task parameter.
typedef struct {
  QueueHandle_t sample_q;
  QueueHandle_t out_q;
  QueueHandle_t sys_q;
  QueueHandle_t ble_q;
  QueueHandle_t time_q;
  TaskHandle_t  imu_acq_task;  // For the supervisor's safe state
  TaskHandle_t  dsp_task;      // For the supervisor's safe state
} app_ctx_t;

#endif  // TYPES_H
