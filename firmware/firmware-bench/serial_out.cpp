/*
 * File:        serial_out.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-18
 * Version:     0.1.2-bench
 * Change:      BOOT adds rearm_frac and rearm_hold_ms (request 0) and rec=0/1.
 *              BOOT was 319 characters, so LINE_MAX_CHARS goes from 352 to
 *              640 and the line buffer becomes static, keeping it off this
 *              task's 4 KB stack. Record mode (request 1): an OUT_SNAP item
 *              prints as SNB, SNR x SNAP_ROWS, SNE (with a CRC-16/CCITT-FALSE
 *              of the SNR lines), waiting for TX space between lines instead
 *              of dropping them, and abandoning the snapshot after
 *              SNAP_SEND_TIMEOUT_MS without space; then the buffer goes back
 *              to snap_free_q. BOOT adds the snapshot settings, HLT adds
 *              snap_drops (record builds only). The heartbeat moved into
 *              heartbeat_if_due() so a long snapshot send keeps it going.
 *              0.1.1-bench: BOOT lists the pitch-sequence and IMU-recovery settings instead
 *              of dominance. EVT gains pitch_seq (DU, UD, D, U or -) and basis
 *              (pitch, roll or z). HLT gains imu_recoveries.
 *              0.1.0-bench: comments only.
 * Description: serial_out task (core 0, same priority as ble_link). Sole owner
 *              of Serial, which is the S3's native USB (USB Serial/JTAG), the
 *              DevKit's "USB" connector. Calls Serial.begin, prints BOOT and the
 *              column headers, then turns each out_q item into one line. Line
 *              format (DRAFT): comma-separated, no spaces, one record per
 *              line, each starting with a type tag. Lines starting with '#'
 *              are comments a parser skips.
 *
 *              Never waits on USB. With the cable in but no program reading
 *              the port, the core's default would block up to ~2 s per write.
 *              Here the write timeout is 0 and a whole line is written only if
 *              it fits in the TX buffer; otherwise it is dropped and counted
 *              (serial_drops in HLT). Lines are never cut in half. The one
 *              exception is a record-mode snapshot: a ~19 KB burst never fits
 *              at once, so its lines wait for space (this task only, core 0),
 *              up to SNAP_SEND_TIMEOUT_MS per line.
 */

// ============================================================================
// Includes
// ============================================================================

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "serial_out.h"
#include "supervisor.h"

// ============================================================================
// Macros
// ============================================================================

#define LINE_MAX_CHARS  640  // Longest line (BOOT, record build) is ~495 characters; up to
                             // 638 print whole
#define SNAP_LINE_CHARS 160  // Snapshot lines are under 64 characters; room for any float

#if RECORD_MODE
// Record-build fields at the end of BOOT (rec=1 and the snapshot settings),
// so a capture records how it was run.
#define BOOT_REC_FMT   ",rec=1,snap_rate=%d,snap_pre=%d,snap_post=%d,snap_rows=%d," \
                       "snap_ch=%s,snap_dec=%d,snap_bufs=%d,snap_gap_us=%d,"         \
                       "snap_timeout_ms=%d"
#define BOOT_REC_ARGS  , SNAP_RATE_HZ, SNAP_N_PRE, SNAP_N_POST, SNAP_ROWS, SNAP_CHANNELS, \
                       SNAP_DECIMALS, SNAP_BUFFERS, SNAP_MAX_GAP_US, SNAP_SEND_TIMEOUT_MS
#else
#define BOOT_REC_FMT   ",rec=0"
#define BOOT_REC_ARGS
#endif

// Serial must be native USB in Hardware CDC mode (decided): on the bench
// DevKit that is the "USB" connector. With other settings Serial becomes UART0
// (the "UART" connector) or the TinyUSB CDC driver instead, and the never-wait
// write code below was written for Hardware CDC only. These come from
// Tools > USB CDC On Boot and Tools > USB Mode.
#if !ARDUINO_USB_CDC_ON_BOOT || !ARDUINO_USB_MODE
#error "Bench build: set Tools > USB CDC On Boot: Enabled and Tools > USB Mode: Hardware CDC and JTAG"
#endif

// ============================================================================
// Globals: types
// ============================================================================

// This task's counters and heartbeat time. Passed to the helpers that can run
// for a while (a snapshot send), so the heartbeat keeps going.
typedef struct {
  int64_t  last_hb_us;    // Time of the last heartbeat
  uint32_t serial_drops;  // Lines dropped: USB TX buffer full (no host reading)
  uint32_t snap_drops;    // Record mode: snapshots abandoned mid-send
} ser_stats_t;

// ============================================================================
// Prototypes
// ============================================================================

static int vformat_line(char *buf, size_t size, const char *fmt, va_list args);
static bool write_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void heartbeat_if_due(const app_ctx_t *ctx, ser_stats_t *st);
static void handle_msg(const out_msg_t *m, const app_ctx_t *ctx, ser_stats_t *st);
#if RECORD_MODE
static int format_line(char *buf, size_t size, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
static bool write_wait(const char *line, int len, const app_ctx_t *ctx, ser_stats_t *st);
static bool send_snapshot(const snap_t *p, const app_ctx_t *ctx, ser_stats_t *st);
#endif
static const char *class_name(evt_class_t c);
static const char *pitch_seq_name(int8_t first, bool seq);
static const char *basis_name(evt_basis_t b);
static const char *fault_name(fault_code_t f);
static const char *link_name(link_event_t w);
static void print_boot(void);
static bool print_msg(const out_msg_t *m);

// ============================================================================
// Implementations
// ============================================================================

/**
 * Formats one line into buf, leaving room for the '\n' the caller appends. A
 * line longer than the buffer is cut short rather than overflowing.
 * @param buf   Output buffer.
 * @param size  Its size in bytes (at least 2).
 * @param fmt   printf-style format, without the newline.
 * @param args  Format arguments.
 * @return Characters in buf (at most size - 2, NUL not counted), or -1 on an
 *         encoding error.
 */
static int vformat_line(char *buf, size_t size, const char *fmt, va_list args) {
  int len = vsnprintf(buf, size - 1, fmt, args);  // Room for '\n'
  if (len < 0) {
    return -1;  // Encoding error: nothing sensible to print
  }
  if (len > (int)size - 2) {
    len = (int)size - 2;  // Truncated: vsnprintf kept what fit
  }
  return len;
}

/**
 * Formats one line and writes it with a trailing newline, but only if the
 * whole line fits in the USB TX buffer right now. Never waits. A line longer
 * than the format buffer is cut short rather than overflowing.
 * @param fmt  printf-style format, without the newline.
 * @param ...  Format arguments.
 * @return true if the line was written, false if it was dropped.
 */
static bool write_line(const char *fmt, ...) {
  // Static: only serial_out_task calls this, one line at a time, and it keeps
  // the buffer off the task's 4 KB stack.
  static char line[LINE_MAX_CHARS];
  va_list args;
  va_start(args, fmt);
  int len = vformat_line(line, sizeof(line), fmt, args);
  va_end(args);
  if (len < 0) {
    return false;
  }
  line[len++] = '\n';
  // Free space only grows while we check (the USB interrupt drains the
  // buffer, and this task is its only writer), so the write below cannot
  // come up short.
  if (Serial.availableForWrite() < len) {
    return false;  // No host reading, or a burst: drop the whole line
  }
  Serial.write((const uint8_t *)line, (size_t)len);
  return true;
}

/**
 * @param c  Event class.
 * @return Its name for the EVT line.
 */
static const char *class_name(evt_class_t c) {
  switch (c) {
    case EVT_POTHOLE: return "POTHOLE";
    case EVT_BUMP:    return "BUMP";
    default:          return "UNKNOWN";
  }
}

/**
 * @param first  First pitch lobe: +1 nose down, -1 nose up, 0 none.
 * @param seq    The opposite lobe followed within the gap.
 * @return D = nose down, U = nose up, two letters if the opposite followed,
 *         "-" if no pitch lobe.
 */
static const char *pitch_seq_name(int8_t first, bool seq) {
  if (first > 0) {
    return seq ? "DU" : "D";
  }
  if (first < 0) {
    return seq ? "UD" : "U";
  }
  return "-";
}

/**
 * @param b  What decided an event's class.
 * @return Its name for the EVT line.
 */
static const char *basis_name(evt_basis_t b) {
  switch (b) {
    case BASIS_PITCH: return "pitch";
    case BASIS_ROLL:  return "roll";
    case BASIS_Z:     return "z";
    default:          return "?";
  }
}

/**
 * @param f  Fault code.
 * @return Its name for the FLT line.
 */
static const char *fault_name(fault_code_t f) {
  switch (f) {
    case FAULT_IMU_INIT:  return "IMU_INIT_FAILED";
    case FAULT_IMU_LOST:  return "IMU_LOST";
    case FAULT_HEARTBEAT: return "HEARTBEAT_MISSED";
    case FAULT_BLE_INIT:  return "BLE_INIT_FAILED";
    default:              return "NONE";
  }
}

/**
 * @param w  Link change.
 * @return Its name for the BLE line.
 */
static const char *link_name(link_event_t w) {
  switch (w) {
    case LINK_ADVERTISING:  return "advertising";
    case LINK_CONNECTED:    return "connected";
    case LINK_DISCONNECTED: return "disconnected";
    case LINK_MTU:          return "mtu";
    case LINK_SUBSCRIBED:   return "subscribed";
    case LINK_UNSUBSCRIBED: return "unsubscribed";
    case LINK_TIME:         return "time";
    case LINK_TIME_BAD:     return "time_bad";
    default:                return "unknown";
  }
}

/**
 * Prints BOOT with every setting in use, so each capture records how it was
 * run, then a '#' header naming the columns of each line type. Runs right
 * after Serial.begin, so the empty TX buffer (SERIAL_TX_BUF_BYTES) holds all
 * of it until a host reads the port; nothing here is dropped.
 * @return None.
 */
static void print_boot(void) {
  write_line("BOOT,fw=%s,odr=%d,acc_fs=%d,gyr_fs=%d,thr_z=%.3f,thr_roll=%.1f,"
             "thr_pitch=%.1f,lockout_ms=%d,rearm_frac=%.2f,rearm_hold_ms=%d,"
             "hpf_hz=%.2f,print_every=%d,"
             "settle_ms=%d,nose_down_sign=%d,pitch_smooth=%d,pitch_lobe=%.1f,"
             "pitch_lobe_end=%.1f,lobe_gap_ms=%d,z_lobe=%.3f,bias_max=%.1f,"
             "imu_retry_ms=%d,imu_good_reads=%d,seg_ms=%d,ble=%s" BOOT_REC_FMT,
             FW_VERSION, IMU_ODR_HZ, ACCEL_FS_G, GYRO_FS_DPS, THR_Z_G,
             THR_ROLL_DPS, THR_PITCH_DPS, LOCKOUT_MS, REARM_FRAC, REARM_HOLD_MS,
             HPF_CUTOFF_HZ,
             PRINT_EVERY_N, SETTLE_MS, PITCH_NOSE_DOWN_SIGN, PITCH_SMOOTH_N,
             PITCH_LOBE_DPS, PITCH_LOBE_END_DPS, PITCH_LOBE_MAX_GAP_MS,
             Z_LOBE_G, GYRO_BIAS_MAX_DPS, IMU_RETRY_MS, IMU_RECOVER_GOOD_READS,
             SEGMENT_MS, BLE_DEVICE_NAME BOOT_REC_ARGS);
  write_line("#S,n,t_us,ax,ay,az,gx,gy,gz,z_hp");
  write_line("#SEG,t_start,t_end,synced,n,rms_z,peak_z,roll_rms,pitch_rms");
  write_line("#EVT,seq,n_peak,t_us_peak,class,peak_z_g,peak_roll_dps,peak_pitch_dps,t_ms,synced,"
             "pitch_seq,basis");
#if RECORD_MODE
  write_line("#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts,"
             "ble_q_drops,ble_sent,ble_fails,serial_drops,imu_recoveries,snap_drops");
  write_line("#SNB,seq,trig_idx,rate_hz,n_pre,n_post,n_rows,channels,fw");
  write_line("#SNR,i,c0,c1,c2");
  write_line("#SNE,seq,n_rows,crc16");
#else
  write_line("#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts,"
             "ble_q_drops,ble_sent,ble_fails,serial_drops,imu_recoveries");
#endif
  write_line("#FLT,code,name");
  write_line("#BLE,what,value");
}

/**
 * Prints one out_q item as one line.
 * @param m  Item from out_q.
 * @return true if the line was written (or the tag is unknown), false if it
 *         was dropped.
 */
static bool print_msg(const out_msg_t *m) {
  switch (m->tag) {
    case OUT_S:
      // 4 decimals for g (0.49 mg/LSB at +-16 g), 2 for dps (17.5 mdps/LSB).
      return write_line("S,%" PRIu32 ",%" PRId64 ",%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.4f",
                        m->s.s.n, m->s.s.t_us, m->s.s.ax, m->s.s.ay, m->s.s.az,
                        m->s.s.gx, m->s.s.gy, m->s.s.gz, m->s.z_hp);
    case OUT_SEG:
      // synced as 1/0. t_start / t_end are phone epoch ms when synced is 1.
      return write_line("SEG,%" PRId64 ",%" PRId64 ",%d,%" PRIu32 ",%.3f,%.3f,%.3f,%.3f",
                        m->seg.t_start_ms, m->seg.t_end_ms, m->seg.synced ? 1 : 0,
                        m->seg.n, m->seg.rms_z, m->seg.peak_z, m->seg.roll_rms,
                        m->seg.pitch_rms);
    case OUT_EVT:
      return write_line("EVT,%" PRIu32 ",%" PRIu32 ",%" PRId64 ",%s,%.3f,%.1f,%.1f,%" PRId64
                        ",%d,%s,%s",
                        m->evt.seq, m->evt.n_peak, m->evt.t_us_peak,
                        class_name(m->evt.cls), m->evt.peak_z_g,
                        m->evt.peak_roll_dps, m->evt.peak_pitch_dps, m->evt.t_ms,
                        m->evt.synced ? 1 : 0,
                        pitch_seq_name(m->evt.pitch_first, m->evt.pitch_seq),
                        basis_name(m->evt.basis));
    case OUT_HLT:
#if RECORD_MODE
      // Record build: snap_drops appended, so the other columns keep their place.
      return write_line("HLT,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
                        ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
                        ",%" PRIu32,
                        m->hlt.t_ms, m->hlt.samples, m->hlt.sample_q_drops,
                        m->hlt.out_q_drops, m->hlt.drdy_timeouts, m->hlt.ble_q_drops,
                        m->hlt.ble_sent, m->hlt.ble_fails, m->hlt.serial_drops,
                        m->hlt.imu_recoveries, m->hlt.snap_drops);
#else
      return write_line("HLT,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
                        ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32,
                        m->hlt.t_ms, m->hlt.samples, m->hlt.sample_q_drops,
                        m->hlt.out_q_drops, m->hlt.drdy_timeouts, m->hlt.ble_q_drops,
                        m->hlt.ble_sent, m->hlt.ble_fails, m->hlt.serial_drops,
                        m->hlt.imu_recoveries);
#endif
    case OUT_FLT:
      return write_line("FLT,F%u,%s", (unsigned)m->flt.code, fault_name(m->flt.code));
    case OUT_BLE:
      return write_line("BLE,%s,%" PRId64, link_name(m->link.what), m->link.value);
    default:
      return true;  // Unknown tag (or OUT_SNAP in a product build): print nothing, not a drop
  }
}

/**
 * Sends this task's heartbeat with its counters, at most once per
 * HEARTBEAT_PERIOD_MS.
 * @param ctx  For sys_q.
 * @param st   Counters and the last heartbeat time, updated.
 * @return None.
 */
static void heartbeat_if_due(const app_ctx_t *ctx, ser_stats_t *st) {
  const int64_t now_us = esp_timer_get_time();
  if (now_us - st->last_hb_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
    st->last_hb_us = now_us;
    sys_msg_t hb = {};
    hb.task = TASK_SERIAL_OUT;
    hb.serial_drops = st->serial_drops;
    hb.snap_drops = st->snap_drops;
    sys_heartbeat(ctx->sys_q, &hb);
  }
}

#if RECORD_MODE
/**
 * CRC-16/CCITT-FALSE (also listed as CRC-16/IBM-3740): polynomial 0x1021,
 * initial value 0xFFFF, no reflection, no final XOR. Bitwise, MSB first.
 * Continues from crc, so one CRC can run over several lines. constexpr so the
 * standard check value is tested at compile time (below). Written here rather
 * than using the ESP32 ROM CRC functions, whose variant would need checking.
 * Parameters: https://reveng.sourceforge.io/crc-catalogue/16.htm
 * @param crc   CRC so far (0xFFFF to start).
 * @param data  Bytes to add.
 * @param len   Number of bytes.
 * @return The updated CRC.
 */
static constexpr uint16_t crc16_ccitt_false(uint16_t crc, const char *data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)((uint8_t)data[i] << 8);  // Next byte into the top 8 bits
    for (int b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

// Standard check value: ASCII "123456789" gives 0x29B1 (handoff, CRC-16
// variant). The GUI checks the same value with binascii.crc_hqx.
static_assert(crc16_ccitt_false(0xFFFF, "123456789", 9) == 0x29B1,
              "CRC-16/CCITT-FALSE check value");

/**
 * printf-style formatting into buf for a snapshot line, leaving room for the
 * '\n' (see vformat_line).
 * @param buf   Output buffer.
 * @param size  Its size in bytes.
 * @param fmt   printf-style format, without the newline.
 * @param ...   Format arguments.
 * @return Characters in buf, or -1 on an encoding error.
 */
static int format_line(char *buf, size_t size, const char *fmt, ...) {
  va_list args;
  va_start(args, fmt);
  const int len = vformat_line(buf, size, fmt, args);
  va_end(args);
  return len;
}

/**
 * Writes one snapshot line (with its '\n'), waiting for room in the USB TX
 * buffer instead of dropping it. Only this task waits, on core 0; it sleeps
 * SNAP_WAIT_MS (at least one tick, never spins) between checks and keeps its
 * heartbeat going, so a slow host is not mistaken for a stuck task (F3).
 * @param line  The line, '\n' included.
 * @param len   Its length.
 * @param ctx   For the heartbeat.
 * @param st    This task's counters (heartbeat time).
 * @return true if written; false if the buffer stayed too full for
 *         SNAP_SEND_TIMEOUT_MS (no host reading), so the snapshot is abandoned.
 */
static bool write_wait(const char *line, int len, const app_ctx_t *ctx, ser_stats_t *st) {
  const int64_t start_us = esp_timer_get_time();
  const TickType_t nap = (pdMS_TO_TICKS(SNAP_WAIT_MS) > 0) ? pdMS_TO_TICKS(SNAP_WAIT_MS) : 1;
  while (Serial.availableForWrite() < len) {
    if (esp_timer_get_time() - start_us >= (int64_t)SNAP_SEND_TIMEOUT_MS * 1000) {
      return false;
    }
    heartbeat_if_due(ctx, st);
    vTaskDelay(nap);
  }
  // As in write_line: free space only grows while we check, so this write
  // cannot come up short.
  Serial.write((const uint8_t *)line, (size_t)len);
  return true;
}

/**
 * Prints one snapshot as an uninterrupted block: SNB, then SNAP_ROWS SNR
 * lines, then SNE. Other lines wait in out_q until it is done. SNE carries
 * the CRC-16/CCITT-FALSE of the exact bytes of every SNR line, in order,
 * without line endings. If any line cannot be written within
 * SNAP_SEND_TIMEOUT_MS, the rest (and SNE) are not sent, so the GUI rejects
 * the partial snapshot. Floats are formatted here on core 0, never on core 1.
 * @param p    Full snapshot. Not returned to the free list here.
 * @param ctx  For the heartbeat.
 * @param st   This task's counters.
 * @return true if SNE went out, false if the snapshot was abandoned.
 */
static bool send_snapshot(const snap_t *p, const app_ctx_t *ctx, ser_stats_t *st) {
  char line[SNAP_LINE_CHARS];

  int len = format_line(line, sizeof(line), "SNB,%" PRIu32 ",%" PRIu32 ",%d,%d,%d,%d,%s,%s",
                        p->seq, p->trig_n, SNAP_RATE_HZ, SNAP_N_PRE, SNAP_N_POST,
                        SNAP_ROWS, SNAP_CHANNELS, FW_VERSION);
  if (len < 0) {
    return false;
  }
  line[len++] = '\n';
  if (!write_wait(line, len, ctx, st)) {
    return false;
  }

  uint16_t crc = 0xFFFF;
  for (int i = 0; i < SNAP_ROWS; i++) {
    const snap_row_t *r = &p->rows[i];
    len = format_line(line, sizeof(line), "SNR,%d,%.*f,%.*f,%.*f", i,
                      SNAP_DECIMALS, r->c[0], SNAP_DECIMALS, r->c[1],
                      SNAP_DECIMALS, r->c[2]);
    if (len < 0) {
      return false;
    }
    crc = crc16_ccitt_false(crc, line, (size_t)len);  // The line without '\n'
    line[len++] = '\n';
    if (!write_wait(line, len, ctx, st)) {
      return false;
    }
  }

  len = format_line(line, sizeof(line), "SNE,%" PRIu32 ",%d,%04X", p->seq, SNAP_ROWS,
                    (unsigned)crc);
  if (len < 0) {
    return false;
  }
  line[len++] = '\n';
  return write_wait(line, len, ctx, st);
}
#endif

/**
 * Handles one out_q item: a snapshot (record mode) is sent and its buffer
 * returned to the free list; anything else becomes one line.
 * @param m    Item from out_q.
 * @param ctx  For snap_free_q and the heartbeat.
 * @param st   This task's counters, updated.
 * @return None.
 */
static void handle_msg(const out_msg_t *m, const app_ctx_t *ctx, ser_stats_t *st) {
#if RECORD_MODE
  if (m->tag == OUT_SNAP) {
    if (!send_snapshot(m->snap, ctx, st)) {
      st->snap_drops++;
    }
    // This task owned the buffer since it left out_q. Cannot fail: the free
    // list has room for every buffer.
    snap_t *p = m->snap;
    xQueueSend(ctx->snap_free_q, &p, 0);
    return;
  }
#else
  (void)ctx;  // Used only in record builds
#endif
  if (!print_msg(m)) {
    st->serial_drops++;
  }
}

void serial_out_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;

  // Native USB. Both calls must come before begin(). A write timeout of 0
  // makes every write return at once; write_line() then only writes lines
  // that fit, so nothing here ever waits on the host. No wait for a host
  // either: the board must run with no monitor attached.
  Serial.setTxBufferSize(SERIAL_TX_BUF_BYTES);
  Serial.setTxTimeoutMs(0);
  Serial.begin(SERIAL_BAUD);
  print_boot();

  ser_stats_t st = {};
  st.last_hb_us = esp_timer_get_time();
  for (;;) {
    out_msg_t m;
    // The timeout lets the heartbeat go out even when out_q stays empty.
    if (xQueueReceive(ctx->out_q, &m, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      handle_msg(&m, ctx, &st);
    }
    heartbeat_if_due(ctx, &st);
  }
}
