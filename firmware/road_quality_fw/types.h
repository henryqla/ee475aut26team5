/*
 * File:        types.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-02
 * Version:     0.1.0-draft (first draft)
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
} fault_code_t;

// Type tag of an out_q item. One item becomes one Serial line.
typedef enum : uint8_t {
  OUT_S = 0,            // Sample line, every PRINT_EVERY_N samples
  OUT_EVT,              // Event line, at the end of each lockout window
  OUT_HLT,              // Health line, once per second
  OUT_FLT,              // Fault line, once per second in the safe state
} out_tag_t;

// Payload of an S line.
typedef struct {
  imu_sample_t s;       // The sample, chip axes
  float        z_hp;    // High-passed vertical acceleration, g
} out_s_t;

// Payload of an EVT line. Peaks are magnitudes.
typedef struct {
  uint32_t    seq;             // Event number, 0 at every boot (CR-6)
  uint32_t    n_peak;          // Sample count at the event's largest peak
  int64_t     t_us_peak;       // Timestamp of that sample
  evt_class_t cls;
  float       peak_z_g;        // Largest |z_hp| in the window, g
  float       peak_roll_dps;   // Largest |roll rate| in the window, dps
  float       peak_pitch_dps;  // Largest |pitch rate| in the window, dps
} out_evt_t;

// Payload of an HLT line.
typedef struct {
  uint32_t t_ms;            // Time since boot, ms
  uint32_t samples;         // Data-ready samples read since boot
  uint32_t sample_q_drops;  // Samples imu_acq could not queue
  uint32_t out_q_drops;     // Lines dsp and supervisor could not queue
  uint32_t drdy_timeouts;   // Times imu_acq waited DRDY_TIMEOUT_MS with no INT1
} out_hlt_t;

// Payload of an FLT line.
typedef struct {
  fault_code_t code;
} out_flt_t;

// Item type of out_q (dsp, supervisor -> serial_out).
typedef struct {
  out_tag_t tag;            // Says which union member is valid
  union {
    out_s_t   s;
    out_evt_t evt;
    out_hlt_t hlt;
    out_flt_t flt;
  };
} out_msg_t;

// Tasks that send heartbeats. Indexes the supervisor's last-heard table.
typedef enum : uint8_t {
  TASK_IMU_ACQ = 0,
  TASK_DSP,
  TASK_SERIAL_OUT,
  TASK_COUNT,
} task_id_t;

// Kind of a sys_q item.
typedef enum : uint8_t {
  SYS_HEARTBEAT = 0,
  SYS_FAULT,
} sys_kind_t;

// Item type of sys_q (every task -> supervisor). Fields a sender does not use
// stay 0.
typedef struct {
  sys_kind_t   kind;
  task_id_t    task;           // Sender
  fault_code_t fault;          // SYS_FAULT only
  uint32_t     samples;        // imu_acq: data-ready samples read since boot
  uint32_t     q_drops;        // imu_acq: sample_q drops. dsp: out_q drops
  uint32_t     drdy_timeouts;  // imu_acq only
} sys_msg_t;

// Queue and task handles, filled once in setup() and read-only afterwards.
// Every task gets a pointer to it as its task parameter.
typedef struct {
  QueueHandle_t sample_q;
  QueueHandle_t out_q;
  QueueHandle_t sys_q;
  TaskHandle_t  imu_acq_task;  // For the supervisor's safe state
  TaskHandle_t  dsp_task;      // For the supervisor's safe state
} app_ctx_t;

#endif  // TYPES_H
