/*
 * File:        types.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      BLE. Segment and event payloads shared by out_q and ble_q,
 *              ble_q and time_q items, link events, F4, ble_link in the
 *              heartbeat table, named heartbeat counters. serial_drops for
 *              native USB Serial on the PCB.
 * Description: Queue item structs, Serial line tags, event classes, fault codes,
 *              and the context struct that hands queue and task handles to every
 *              task. Tasks share nothing else.
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
  int64_t  t_us;        // esp_timer_get_time() when imu_acq woke for this sample
  float    ax, ay, az;  // Acceleration, g
  float    gx, gy, gz;  // Angular rate, dps
} imu_sample_t;

// Event class, decided at the end of the lockout window.
typedef enum : uint8_t {
  EVT_UNKNOWN = 0,
  EVT_POTHOLE,          // Roll-dominant
  EVT_BUMP,             // Pitch-dominant
} evt_class_t;

// Fault codes. Every fault leads to the same safe state; the code is only
// reported, never branched on.
typedef enum : uint8_t {
  FAULT_NONE      = 0,
  FAULT_IMU_INIT  = 1,  // F1: begin or WHO_AM_I failed at boot
  FAULT_IMU_LOST  = 2,  // F2: WHO_AM_I mismatch at runtime, or no data for IMU_STALL_MS
  FAULT_HEARTBEAT = 3,  // F3: a task missed heartbeats for HEARTBEAT_TIMEOUT_MS
  FAULT_BLE_INIT  = 4,  // F4: BLE stack, service, or advertising failed to start
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

// One event (contract section 4.2), plus the local fields the Serial EVT line
// prints. Peaks are magnitudes.
typedef struct {
  uint32_t    seq;             // Event number for the Serial EVT line, 0 at every
                               // boot. BLE messages get their own seq in ble_link
  uint32_t    n_peak;          // Sample count at the event's largest peak
  int64_t     t_us_peak;       // Local timestamp of that sample
  int64_t     t_ms;            // Same instant in phone epoch ms if synced, else local ms
  bool        synced;          // Offset in use when the peak was recorded (CR-4)
  evt_class_t cls;
  float       peak_z_g;        // Largest |z_hp| in the window, g
  float       peak_roll_dps;   // Largest |roll rate| in the window, dps
  float       peak_pitch_dps;  // Largest |pitch rate| in the window, dps
} evt_t;

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
  OUT_S = 0,            // Sample line, every PRINT_EVERY_N samples
  OUT_EVT,              // Event line, at the end of each lockout window
  OUT_SEG,              // Segment line, once per second
  OUT_HLT,              // Health line, once per second
  OUT_FLT,              // Fault line, once per second in the safe state
  OUT_BLE,              // BLE link line, on each link change
} out_tag_t;

// Payload of an S line.
typedef struct {
  imu_sample_t s;       // The sample, chip axes
  float        z_hp;    // High-passed vertical acceleration, g
} out_s_t;

// Payload of an HLT line.
typedef struct {
  uint32_t t_ms;            // Time since boot, ms
  uint32_t samples;         // Data-ready samples read since boot
  uint32_t sample_q_drops;  // Samples imu_acq could not queue
  uint32_t out_q_drops;     // Lines dsp, ble_link and supervisor could not queue
  uint32_t drdy_timeouts;   // Times imu_acq waited DRDY_TIMEOUT_MS with no INT1
  uint32_t ble_q_drops;     // Segments and events dsp could not queue for BLE
  uint32_t ble_sent;        // Notifications sent
  uint32_t ble_fails;       // Notifications that failed or were too long
  uint32_t serial_drops;    // Lines serial_out dropped: USB TX buffer full (no host reading)
} out_hlt_t;

// Payload of an FLT line.
typedef struct {
  fault_code_t code;
} out_flt_t;

// Item type of out_q (dsp, ble_link, supervisor -> serial_out).
typedef struct {
  out_tag_t tag;            // Says which union member is valid
  union {
    out_s_t   s;
    evt_t     evt;
    seg_t     seg;
    out_hlt_t hlt;
    out_flt_t flt;
    link_t    link;
  };
} out_msg_t;

// Type tag of a ble_q item.
typedef enum : uint8_t {
  BLE_SEG = 0,          // From dsp: send as a seg message
  BLE_EVT,              // From dsp: send as an evt message
  BLE_LINK,             // From the NimBLE callbacks: link changed
  BLE_IMU_DOWN,         // From supervisor, each second in the safe state
} ble_tag_t;

// Item type of ble_q (dsp, NimBLE callbacks, supervisor -> ble_link).
typedef struct {
  ble_tag_t tag;            // Says which union member is valid
  union {
    seg_t  seg;
    evt_t  evt;
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
  TASK_COUNT,
} task_id_t;

// Kind of a sys_q item.
typedef enum : uint8_t {
  SYS_HEARTBEAT = 0,
  SYS_FAULT,
} sys_kind_t;

// Item type of sys_q (every task -> supervisor). Each sender fills only the
// counters it owns; the rest stay 0.
typedef struct {
  sys_kind_t   kind;
  task_id_t    task;            // Sender
  fault_code_t fault;           // SYS_FAULT only
  uint32_t     samples;         // imu_acq
  uint32_t     drdy_timeouts;   // imu_acq
  uint32_t     sample_q_drops;  // imu_acq
  uint32_t     out_q_drops;     // dsp, ble_link
  uint32_t     ble_q_drops;     // dsp
  uint32_t     ble_sent;        // ble_link
  uint32_t     ble_fails;       // ble_link
  uint32_t     serial_drops;    // serial_out
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
