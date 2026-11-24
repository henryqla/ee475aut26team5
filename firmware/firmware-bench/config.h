/*
 * File:        config.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-23
 * Version:     0.1.4-bench
 * Change:      0.1.4-bench (manual-recording-overhaul-handoff.md, request A;
 *              owner, 2026-11-23): one build that streams every sample. The
 *              detection settings (THR_*, LOCKOUT_MS, REARM_*, EVT_CONF,
 *              PITCH_*, Z_LOBE_G), the record and log build flags
 *              (RECORD_MODE, LOG_MODE) with every SNAP_* setting, and
 *              PRINT_EVERY_N are gone. The log build's values are now the only
 *              ones: SERIAL_TX_BUF_BYTES 32768 and OUT_Q_LEN 256 (were
 *              LOG_TX_BUF_BYTES and LOG_OUT_Q_LEN), L_DECIMALS (was
 *              LOG_DECIMALS), PERF_PERIOD_MS (was LOG_PERF_PERIOD_MS).
 *              0.1.3-bench: log build (LOG_MODE, request 4); bench mapping with
 *              chip Y forward (2026-11-23); CFG_PERIOD_MS.
 *              0.1.2-bench: re-arm rule (REARM_*), record mode (RECORD_MODE,
 *              SNAP_*).
 *              0.1.1-bench: pitch-sequence classification settings, IMU
 *              recovery settings (IMU_RETRY_MS, IMU_RECOVER_GOOD_READS).
 *              0.1.0-bench: bench build for the ESP32-S3-DevKitC-1, copied
 *              from road_quality_fw 0.2.0-draft (commit 4276b56).
 *              The removed code is in git, tag pre-manual-overhaul.
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

#define FW_VERSION            "0.1.4-bench"  // Printed in BOOT and sent in status. Must not
                                             // start with "fake". Bench trips upload as REAL
                                             // and Ben deletes them by this version, so tell
                                             // Henry before changing it

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

// Serial is the S3's native USB (USB Serial/JTAG, GPIO19/20): the DevKit's "USB"
// connector. Its "UART" connector (bridge on GPIO43/44) is not used for Serial.
// Every sample goes out as one L line (~44 kB/s) for the record GUI in
// firmware/cnn/cnn_record_gui/; the board decides nothing about events.
#define SERIAL_BAUD           115200   // Ignored by native USB; passed only because
                                       // Serial.begin() takes a baud rate
#define SERIAL_TX_BUF_BYTES   32768    // DRAFT (L3). ~0.75 s of the L stream. Holds BOOT until
                                       // a host reads it, and absorbs bursts
#define L_DECIMALS            4        // DRAFT. Every float in an L line: 0.1 mg and 0.1 mdps,
                                       // below the sensor noise
#define CFG_PERIOD_MS         5000     // DRAFT. serial_out repeats the BOOT settings as a CFG
                                       // line this often, so a PC that connects after BOOT
                                       // went by still gets them. BOOT goes out only once, at
                                       // reset, usually before the PC can reopen the port
                                       // (native USB re-enumerates on every reset)
#define PERF_PERIOD_MS        10000    // DRAFT (owner, 2026-11-22). serial_out prints a
                                       // "#LPERF" comment line this often: L lines handled, the
                                       // average and largest time to format and write one, and
                                       // the lowest free heap so far

// ============================================================================
// Macros: signal processing
// ============================================================================

// The model channels (z_hp, roll, pitch) come from here, and live inference
// will use the same code, so none of this changes without a version bump.
#define HPF_CUTOFF_HZ         0.5f     // DECIDED (approximate). Gives a ~= 0.99250 at 416 Hz
#define SETTLE_MS             2000     // DRAFT. ~6 filter time constants (RC ~= 0.32 s). The
                                       // gyro bias is measured over this time; L lines carry
                                       // ok = 0 until it has passed
#define GYRO_BIAS_MAX_DPS     10.0f    // DRAFT. Roll and pitch bias is the mean over the
                                       // settle time. A larger mean means the board moved,
                                       // so no bias is subtracted
#define SEGMENT_MS            1000     // CONTRACT 4.1. One seg per 1 s window of local time

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
#define IMU_RETRY_MS          500      // DRAFT. While the IMU is down (F1/F2), imu_acq checks
                                       // WHO_AM_I this often and sends a heartbeat
#define IMU_RECOVER_GOOD_READS 3       // DRAFT. Correct WHO_AM_I reads in a row before
                                       // re-initializing, so a flickering wire settles first
#define HEARTBEAT_PERIOD_MS   1000     // 1 Hz heartbeats and WHO_AM_I check (from the doc's task table)
#define SUPERVISOR_PERIOD_MS  1000     // supervisor period: one HLT or FLT line per second
#define HEARTBEAT_TIMEOUT_MS  3000     // DRAFT. Three missed 1 Hz heartbeats = F3

// ============================================================================
// Macros: queues (lengths in items)
// ============================================================================

#define SAMPLE_Q_LEN          104      // DRAFT. 0.25 s of samples
#define OUT_Q_LEN             256      // DRAFT (L3). ~0.6 s of L lines, ~64 bytes per item
                                       // (~16 KB). Check out_q_drops in HLT
#define SYS_Q_LEN             16       // DRAFT. From the architecture doc
#define BLE_Q_LEN             16       // DRAFT. ~1 item/s from dsp plus link changes

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
