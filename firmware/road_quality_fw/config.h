/*
 * File:        config.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      BLE settings (contract section 3), segment length, ble_q
 *              length, ble_link priority. PCB target: Serial over native USB
 *              with its own TX buffer size.
 * Description: Every tunable number in the firmware. No tunable appears in any
 *              other file. Values come from devkit-phase1-context.md v0.4,
 *              "Draft settings", and docs/data-contract.md. Anything not marked
 *              DECIDED or CONTRACT is a DRAFT placeholder: it lets the draft
 *              build and run, and stays undecided until Henry sets it.
 */

#ifndef CONFIG_H
#define CONFIG_H

// ============================================================================
// Macros: firmware identity
// ============================================================================

#define FW_VERSION            "0.2.0-draft"  // Printed in BOOT and sent in status. Bump on
                                             // every change. Must not start with "fake"

// ============================================================================
// Macros: IMU
// ============================================================================

#define IMU_ODR_HZ            416      // DECIDED. Accel and gyro output data rate
#define ACCEL_FS_G            16       // DRAFT. Headroom for impact spikes. Also valid: 4, 8, 32
#define GYRO_FS_DPS           500      // DRAFT. Matches the data contract's gyro range
#define SPI_CLOCK_HZ          1000000  // DRAFT. Same as PCB bring-up; the part allows 10 MHz.
                                       // One library read is ~152 bits (~63 kbit/s at
                                       // 416 Hz), so ~16x headroom

// ============================================================================
// Macros: Serial output
// ============================================================================

// Serial is the S3's native USB (USB Serial/JTAG): the PCB has no UART bridge.
#define SERIAL_BAUD           115200   // Ignored by native USB; passed only because
                                       // Serial.begin() takes a baud rate
#define SERIAL_TX_BUF_BYTES   4096     // DRAFT. ~1 s of output. Holds BOOT until a host
                                       // reads it, and absorbs bursts
#define PRINT_EVERY_N         8        // DRAFT. 52 S lines/s, ~3.6 kB/s

// ============================================================================
// Macros: detection
// ============================================================================

#define HPF_CUTOFF_HZ         0.5f     // DECIDED (approximate). Gives a ~= 0.99250 at 416 Hz
#define SETTLE_MS             2000     // DRAFT. ~6 filter time constants (RC ~= 0.32 s)
#define THR_Z_G               1.0f     // DRAFT, bench only. A firm tap passes, noise does not
#define THR_ROLL_DPS          60.0f    // DRAFT, bench only. A quick hand tilt passes
#define THR_PITCH_DPS         60.0f    // DRAFT, bench only. Same as roll
#define LOCKOUT_MS            1250     // DRAFT. Middle of the DECIDED 1.0 to 1.5 s range
#define DOMINANCE_RATIO       1.5f     // DRAFT. One axis must clearly dominate, else UNKNOWN
#define SEGMENT_MS            1000     // CONTRACT 4.1. One seg per 1 s window of local time
#define EVT_CONF              1.0f     // CONTRACT 4.2. The detector has no confidence measure

// ============================================================================
// Macros: BLE (data contract section 3)
// ============================================================================

#define BLE_DEVICE_NAME       "RoadQuality"  // CONTRACT. Sent in the scan response
#define BLE_MTU               247      // CONTRACT. The MTU the app asks for
#define BLE_MAX_PAYLOAD       (BLE_MTU - 3)  // 244 bytes per notification; longer is cut, not split
#define BLE_ADV_INTERVAL      160      // CONTRACT (~100 ms). Units of 0.625 ms

// ============================================================================
// Macros: timing and fault detection
// ============================================================================

#define DRDY_TIMEOUT_MS       10       // DRAFT. ~4 sample periods without INT1, then read anyway
#define IMU_STALL_MS          100      // DRAFT. No data-ready sample for ~42 periods = F2
#define HEARTBEAT_PERIOD_MS   1000     // 1 Hz heartbeats and WHO_AM_I check (from the doc's task table)
#define SUPERVISOR_PERIOD_MS  1000     // supervisor period: one HLT or FLT line per second
#define HEARTBEAT_TIMEOUT_MS  3000     // DRAFT. Three missed 1 Hz heartbeats = F3

// ============================================================================
// Macros: queues (lengths in items)
// ============================================================================

#define SAMPLE_Q_LEN          104      // DRAFT. 0.25 s of samples
#define OUT_Q_LEN             32       // DRAFT. Placeholder; check out_q_drops in HLT
#define SYS_Q_LEN             16       // DRAFT. From the architecture doc
#define BLE_Q_LEN             16       // DRAFT. ~2 items/s from dsp plus link changes

// ============================================================================
// Macros: tasks
// ============================================================================

#define TASK_STACK_BYTES      4096     // DRAFT. ESP32 FreeRTOS stack sizes are in bytes. Trim later

// Only the order matters. All sit above Arduino's loopTask (priority 1).
#define PRIO_IMU_ACQ          5
#define PRIO_DSP              4
#define PRIO_SERIAL_OUT       3
#define PRIO_BLE_LINK         3        // DRAFT. Same as serial_out: both spend almost all
                                       // their time blocked, so neither starves the other
#define PRIO_SUPERVISOR       2

#define CORE_SAMPLING         1        // DECIDED. imu_acq and dsp only
#define CORE_IO               0        // DECIDED. serial_out, ble_link, supervisor: Serial and
                                       // wireless stay off core 1. NimBLE's own host and
                                       // controller tasks are also on core 0 (core sdkconfig)

#endif  // CONFIG_H
