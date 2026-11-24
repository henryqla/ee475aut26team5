/*
 * File:        config.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-22
 * Version:     0.1.3-bench
 * Change:      FW_VERSION 0.1.3-bench (owner, 2026-11-22). Log build
 *              (drive-log-manual-record-handoff.md v0.2, request 4, L1, L3):
 *              LOG_MODE, LOG_DECIMALS, LOG_TX_BUF_BYTES, LOG_OUT_Q_LEN and
 *              LOG_PERF_PERIOD_MS. Bench mapping (2026-11-23): chip Y now
 *              points forward (pitch = chip X gyro), so PITCH_NOSE_DOWN_SIGN
 *              is -1; SNAP_CHANNELS comment updated. CFG line (2026-11-23):
 *              CFG_PERIOD_MS.
 *              0.1.2-bench: Re-arm rule (cnn-record-train-handoff.md
 *              v0.4, request 0, C16): REARM_FRAC, REARM_HOLD_MS. Record mode
 *              (request 1): RECORD_MODE and the SNAP_* settings.
 *              0.1.1-bench: Pitch-sequence classification settings
 *              (PITCH_*, Z_LOBE_G, GYRO_BIAS_MAX_DPS) replace DOMINANCE_RATIO.
 *              IMU recovery settings (IMU_RETRY_MS, IMU_RECOVER_GOOD_READS).
 *              0.1.0-bench: bench build for the ESP32-S3-DevKitC-1, copied
 *              from road_quality_fw 0.2.0-draft (commit 4276b56).
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

#define FW_VERSION            "0.1.3-bench"  // Printed in BOOT and sent in status. Must not
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
#define SERIAL_BAUD           115200   // Ignored by native USB; passed only because
                                       // Serial.begin() takes a baud rate
#define SERIAL_TX_BUF_BYTES   4096     // DRAFT. ~1 s of output. Holds BOOT until a host
                                       // reads it, and absorbs bursts. The log build uses
                                       // LOG_TX_BUF_BYTES instead
#define PRINT_EVERY_N         8        // DRAFT. 52 S lines/s, ~3.6 kB/s. No S lines in the
                                       // log build (it sends an L line for every sample)
#define CFG_PERIOD_MS         5000     // DRAFT. serial_out repeats the BOOT settings as a CFG
                                       // line this often (~0.5 kB each), so a PC that connects
                                       // after BOOT went by still gets them. BOOT goes out only
                                       // once, at reset, usually before the PC can reopen the
                                       // port (native USB re-enumerates on every reset)

// ============================================================================
// Macros: detection
// ============================================================================

#define HPF_CUTOFF_HZ         0.5f     // DECIDED (approximate). Gives a ~= 0.99250 at 416 Hz
#define SETTLE_MS             2000     // DRAFT. ~6 filter time constants (RC ~= 0.32 s)
#define THR_Z_G               1.0f     // DRAFT, bench only. A firm tap passes, noise does not
#define THR_ROLL_DPS          60.0f    // DRAFT, bench only. A quick hand tilt passes
#define THR_PITCH_DPS         60.0f    // DRAFT, bench only. Same as roll
#define LOCKOUT_MS            1250     // DRAFT. Middle of the DECIDED 1.0 to 1.5 s range
#define REARM_FRAC            0.5f     // DRAFT (C16). After a lockout ends, no new window opens
                                       // until every axis is below this fraction of its own
                                       // threshold (absolute values, like the trigger), so the
                                       // tail of one event cannot fire a second. Must be in
                                       // (0, 1]; dsp.cpp fails the build otherwise
#define REARM_HOLD_MS         200      // DRAFT (owner, 2026-11-18). ... and has stayed below it
                                       // this long, so a zero crossing of a ringing or shaken
                                       // signal does not re-arm. Covers steady oscillation down
                                       // to ~0.8 Hz at threshold amplitude. Quiet time before
                                       // the lockout ends counts. 0 = re-arm on the first quiet
                                       // sample (the rule without a hold)
#define SEGMENT_MS           1000     // CONTRACT 4.1. One seg per 1 s window of local time
#define EVT_CONF              1.0f     // CONTRACT 4.2. The detector has no confidence measure

// ============================================================================
// Macros: classification (pothole vs. speed bump)
// ============================================================================

// The front axle decides: a pothole pitches the nose down then up, a speed bump
// up then down. The rear axle does the opposite, so only the FIRST pitch lobe
// near the event counts. Fallbacks when no pitch lobe is seen: roll at its
// threshold = pothole (one wheel), else the sign of the first vertical
// excursion (drop = pothole, push up = bump). Every event is POTHOLE or BUMP.
#define PITCH_NOSE_DOWN_SIGN  (-1)     // DRAFT. +1 if positive pitch rate (chip gx, see
                                       // map_axes in dsp.cpp) is nose down. Right-hand rule
                                       // with chip Y forward, Z up (so X to the right) says -1.
                                       // Bench check: tip the Y-arrow end down quickly; gx must
                                       // go negative, else set +1
#define PITCH_SMOOTH_N        4        // DRAFT. Moving average length for the lobe tracker
                                       // (~10 ms at 416 Hz), so vibration does not flip sign
#define PITCH_LOBE_DPS        30.0f    // DRAFT, bench only. Smoothed |pitch rate| that starts
                                       // a lobe. Half of THR_PITCH_DPS
#define PITCH_LOBE_END_DPS    15.0f    // DRAFT. A lobe ends below this (hysteresis)
#define PITCH_LOBE_MAX_GAP_MS 300      // DRAFT. Longest gap from the end of the first lobe
                                       // to the start of the opposite one, for the two to
                                       // count as one down-up or up-down sequence. Also how
                                       // long before a window opens a lobe may have ended
                                       // and still be the event's first lobe
#define Z_LOBE_G              0.5f     // DRAFT, bench only. |z_hp| that sets the first
                                       // vertical excursion's sign. Half of THR_Z_G
#define GYRO_BIAS_MAX_DPS     10.0f    // DRAFT. Roll and pitch bias is the mean over the
                                       // settle time. A larger mean means the board moved,
                                       // so no bias is subtracted

// ============================================================================
// Macros: record mode (CNN data, cnn-record-train-handoff.md v0.4)
// ============================================================================

// Record build: every trigger's snapshot of the model channels goes to Serial
// as SNB / SNR / SNE lines for the desktop record GUI. Detection, EVT, SEG and
// BLE are the same as in the product build. Snapshots never go over BLE.
#define RECORD_MODE           0        // DECIDED (C6): compile-time flag, off by default.
                                       // 1 = record build (set locally to record; commit 0)
#if RECORD_MODE != 0 && RECORD_MODE != 1
#error "RECORD_MODE must be 0 or 1"
#endif
#define SNAP_RATE_HZ          IMU_ODR_HZ  // DRAFT (C5). No decimation: data can be decimated
                                       // later in Python, never un-decimated
#define SNAP_PRE_MS           250      // DECIDED (C4). Captures the onset before the crossing
#define SNAP_POST_MS          LOCKOUT_MS  // DECIDED (C4). Tied to the lockout, so a snapshot
                                       // is full when its window closes and two never overlap
#define SNAP_N_PRE            ((SNAP_PRE_MS * SNAP_RATE_HZ) / 1000)   // 104 rows before the trigger
#define SNAP_N_POST           ((SNAP_POST_MS * SNAP_RATE_HZ) / 1000)  // 520 rows from the trigger on
#define SNAP_ROWS             (SNAP_N_PRE + SNAP_N_POST)              // 624 rows (C4)
#define SNAP_N_CH             3        // DECIDED (C3). Channels per row
#define SNAP_CHANNELS         "az_hp/gx/gy"  // DECIDED (C3), bench mapping in map_axes()
                                       // (dsp.cpp, the one mapping table): vertical accel after
                                       // the HPF (chip Z, g), roll rate (chip Y gyro, dps),
                                       // pitch rate (chip X gyro, dps). Gyro with the settle-time
                                       // bias removed, exactly as the trigger sees it (C7). The
                                       // name dates from when chip X pointed forward (before
                                       // 2026-11-23). The columns are always z_hp, roll, pitch,
                                       // so with this mapping "gx" holds chip gy and "gy" holds
                                       // chip gx. Renaming it means changing TAKE_CHANNELS in
                                       // cnn_config.py too
#define SNAP_DECIMALS         4        // DRAFT. 0.1 mg and 0.1 mdps, below the sensor noise
#define SNAP_BUFFERS          2        // DRAFT. One filling while one sends, ~7.5 KB each
#define SNAP_MAX_GAP_US       ((3 * 1000000) / (2 * IMU_ODR_HZ))  // DRAFT. 1.5 sample periods
                                       // (3605 us). A longer gap between two samples means some
                                       // were missed; a snapshot across it is dropped, never
                                       // sent with a hole in it
#define SNAP_SEND_TIMEOUT_MS  2000     // DRAFT. serial_out abandons a snapshot (no SNE) if the
                                       // USB TX buffer stays full this long, e.g. no host reading
#define SNAP_WAIT_MS          1        // DRAFT. serial_out's sleep between checks for TX space

// ============================================================================
// Macros: log mode (CNN data, drive-log-manual-record-handoff.md v0.2)
// ============================================================================

// Log build: every sample goes to Serial as an L line (raw chip axes plus the
// model channels exactly as a snapshot row holds them, and an ok flag) for the
// record GUI's drive log and manual Record. No S lines and no snapshots: every
// snapshot can be cut from the log. Detection, EVT, SEG, BLE and the safe
// state are the same as in the product build.
#define LOG_MODE              1        // DECIDED (L1): compile-time flag, off by default.
                                       // 1 = log build (set locally to log; commit 0)
#if LOG_MODE != 0 && LOG_MODE != 1
#error "LOG_MODE must be 0 or 1"
#endif
#if LOG_MODE && RECORD_MODE
#error "LOG_MODE and RECORD_MODE cannot both be 1: snapshots would compete with the L stream for out_q, and every snapshot can be cut from the log"
#endif
#define LOG_DECIMALS          SNAP_DECIMALS  // Every float in an L line. Must equal
                                       // SNAP_DECIMALS, so z_hp, roll and pitch print exactly as
                                       // SNR rows do and a window cut from L lines equals the
                                       // snapshot text
#define LOG_TX_BUF_BYTES      32768    // DRAFT (L3). ~0.75 s of the ~44 kB/s L stream; replaces
                                       // SERIAL_TX_BUF_BYTES in the log build only
#define LOG_OUT_Q_LEN         256      // DRAFT (L3). ~0.6 s of samples; replaces OUT_Q_LEN in
                                       // the log build only. ~64 bytes per item
#define LOG_PERF_PERIOD_MS    10000    // DRAFT (owner, 2026-11-22). serial_out prints a
                                       // "#LPERF" comment line this often: L lines handled, the
                                       // average and largest time to format and write one, and
                                       // the lowest free heap so far (evidence for LOG-2, LOG-3)

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
#define OUT_Q_LEN             32       // DRAFT. Placeholder; check out_q_drops in HLT. The log
                                       // build uses LOG_OUT_Q_LEN instead
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
