/*
 * File:        serial_out.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-24
 * Version:     0.1.5-bench
 * Change:      0.1.5-bench (training-live-inference-handoff.md, request F):
 *              PROB and DET lines; a SELFTEST line right after BOOT; BOOT and
 *              CFG add the model fields (model, classes, window, hop,
 *              det_conf, det_lockout_ms, arena, arena_used, selftest,
 *              selftest_diff, inf); HLT appends inferences, inf_skips,
 *              lat_last_us, lat_max_us; FLT names for F5-F8. The L line and
 *              the never-wait writer are unchanged.
 *              0.1.4-bench (manual-recording-overhaul-handoff.md, request A):
 *              one build. Removed: the snapshot sender (SNB / SNR / SNE, its
 *              CRC and the wait-for-space writer), EVT and S lines and their
 *              name helpers, the record / log build branches, and snap_drops
 *              in HLT and heartbeats (git tag pre-manual-overhaul). BOOT and
 *              CFG drop the thresholds, lockout, re-arm, lobe and build-flag
 *              fields and add the axis mapping (dsp_axis_map()), bias_max and
 *              the buffer sizes. The L line, its whole-line-or-nothing rule,
 *              #LPERF and CFG are unchanged.
 *              0.1.3-bench: L lines (log build), #LPERF, n_trig in EVT, CFG
 *              lines every CFG_PERIOD_MS.
 *              0.1.2-bench: snapshots (record build), static line buffer.
 *              0.1.1-bench: pitch-sequence and IMU-recovery fields.
 *              0.1.0-bench: comments only.
 * Description: serial_out task (core 0, same priority as ble_link). Sole owner
 *              of Serial, which is the S3's native USB (USB Serial/JTAG), the
 *              DevKit's "USB" connector. Calls Serial.begin, prints BOOT and the
 *              column headers, then turns each out_q item into one line, and
 *              repeats BOOT's settings as a CFG line every CFG_PERIOD_MS. Line
 *              format (DRAFT): comma-separated, no spaces, one record per
 *              line, each starting with a type tag. Lines starting with '#'
 *              are comments a parser skips.
 *
 *              Never waits on USB. With the cable in but no program reading
 *              the port, the core's default would block up to ~2 s per write.
 *              Here the write timeout is 0 and a whole line is written only if
 *              it fits in the TX buffer; otherwise it is dropped and counted
 *              (serial_drops in HLT). Lines are never cut in half.
 */

// ============================================================================
// Includes
// ============================================================================

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include "esp_system.h"  // esp_get_minimum_free_heap_size() for #LPERF
#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "dsp.h"         // dsp_axis_map() for BOOT
#include "inference.h"   // Model facts for BOOT / CFG, class names for DET
#include "serial_out.h"
#include "supervisor.h"

// ============================================================================
// Macros
// ============================================================================

#define LINE_MAX_CHARS  640  // Longest line (BOOT / CFG) is ~430 characters; up to
                             // 638 print whole

// Every setting in use, as key=value fields: BOOT's fields after its tag, and
// the same text again in every CFG line (print_settings()). The %s after
// bias_max is the axis mapping, itself three fields (vert=..,roll=..,pitch=..).
// The model fields (request F) come from inference_boot(): classes are
// '|'-separated because commas separate the fields; inf is "ok", or the model
// fault (F5-F8) that stopped inference.
#define SETTINGS_FMT   "fw=%s,odr=%d,acc_fs=%d,gyr_fs=%d,hpf_hz=%.2f,settle_ms=%d,"            \
                       "bias_max=%.1f,%s,l_dec=%d,tx_buf=%d,out_q=%d,perf_ms=%d,"              \
                       "imu_retry_ms=%d,imu_good_reads=%d,seg_ms=%d,cfg_ms=%d,ble=%s,"         \
                       "model=%s,classes=%s,window=%" PRIu32 ",hop=%" PRIu32 ","               \
                       "det_conf=%.2f,det_lockout_ms=%d,arena=%d,arena_used=%" PRIu32 ","      \
                       "selftest=%s,selftest_diff=%.6f,inf=%s"
#define SETTINGS_ARGS  FW_VERSION, IMU_ODR_HZ, ACCEL_FS_G, GYRO_FS_DPS, HPF_CUTOFF_HZ,         \
                       SETTLE_MS, GYRO_BIAS_MAX_DPS, dsp_axis_map(), L_DECIMALS,               \
                       SERIAL_TX_BUF_BYTES, OUT_Q_LEN, PERF_PERIOD_MS, IMU_RETRY_MS,           \
                       IMU_RECOVER_GOOD_READS, SEGMENT_MS, CFG_PERIOD_MS, BLE_DEVICE_NAME,     \
                       inference_boot()->model_version, inference_boot()->classes,             \
                       inference_boot()->window, inference_boot()->hop, DET_CONF,              \
                       DET_LOCKOUT_MS, TENSOR_ARENA_BYTES, inference_boot()->arena_used,       \
                       selftest_text(), inference_boot()->selftest_diff, inf_state_text()

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

// This task's counters and timers.
typedef struct {
  int64_t  last_hb_us;    // Time of the last heartbeat
  int64_t  last_cfg_us;   // Time of the last CFG line (or of boot, before the first)
  uint32_t serial_drops;  // Lines dropped: USB TX buffer full (no host reading)
  int64_t  perf_start_us; // Start of the current #LPERF period
  uint32_t l_lines;       // L lines handled (written or dropped) in this period
  uint64_t l_sum_us;      // Their total format-and-write time, us
  uint32_t l_max_us;      // The longest one, us
} ser_stats_t;

// ============================================================================
// Prototypes
// ============================================================================

static int vformat_line(char *buf, size_t size, const char *fmt, va_list args);
static bool write_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void heartbeat_if_due(const app_ctx_t *ctx, ser_stats_t *st);
static void handle_msg(const out_msg_t *m, ser_stats_t *st);
static void perf_if_due(ser_stats_t *st);
static const char *fault_name(fault_code_t f);
static const char *link_name(link_event_t w);
static const char *selftest_text(void);
static const char *inf_state_text(void);
static bool print_settings(const char *tag);
static void print_boot(void);
static void cfg_if_due(ser_stats_t *st);
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
 * @param f  Fault code.
 * @return Its name for the FLT line.
 */
static const char *fault_name(fault_code_t f) {
  switch (f) {
    case FAULT_IMU_INIT:  return "IMU_INIT_FAILED";
    case FAULT_IMU_LOST:  return "IMU_LOST";
    case FAULT_HEARTBEAT: return "HEARTBEAT_MISSED";
    case FAULT_BLE_INIT:  return "BLE_INIT_FAILED";
    case FAULT_MODEL_SCHEMA: return "MODEL_SCHEMA_MISMATCH";
    case FAULT_ARENA:     return "ARENA_ALLOC_FAILED";
    case FAULT_INVOKE:    return "INVOKE_FAILED";
    case FAULT_SELFTEST:  return "SELFTEST_FAILED";
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
 * @return The boot self-test result for BOOT, CFG and SELFTEST: "pass",
 *         "fail", or "not_run" (the model did not load).
 */
static const char *selftest_text(void) {
  const inference_boot_t *b = inference_boot();
  return !b->selftest_ran ? "not_run" : (b->selftest_pass ? "pass" : "fail");
}

/**
 * @return "ok" while inference runs, otherwise the model fault that stopped
 *         it, e.g. "F8". Only serial_out calls it, so one static buffer is
 *         enough.
 */
static const char *inf_state_text(void) {
  static char buf[8];
  const fault_code_t f = inference_fault();
  if (f == FAULT_NONE) {
    return "ok";
  }
  snprintf(buf, sizeof(buf), "F%u", (unsigned)f);
  return buf;
}

/**
 * Prints one line with every setting in use: the tag, then SETTINGS_FMT's
 * key=value fields. BOOT and CFG lines differ only in the tag.
 * @param tag  "BOOT" or "CFG".
 * @return true if the line was written, false if it was dropped.
 */
static bool print_settings(const char *tag) {
  return write_line("%s," SETTINGS_FMT, tag, SETTINGS_ARGS);
}

/**
 * Prints BOOT with every setting in use, so each capture records how it was
 * made, then a '#' header naming the columns of each line type. Runs right
 * after Serial.begin, so the empty TX buffer (SERIAL_TX_BUF_BYTES) holds all
 * of it until a host reads the port; nothing here is dropped. A host can
 * still miss BOOT (it connects later, or opening the port discards what was
 * waiting), so cfg_if_due() repeats the settings.
 * @return None.
 */
static void print_boot(void) {
  print_settings("BOOT");
  write_line("SELFTEST,%s,%.6f", selftest_text(), inference_boot()->selftest_diff);
  write_line("#L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok");
  write_line("#LPERF,t_ms,lines,avg_us,max_us,heap_min");
  write_line("#SEG,t_start,t_end,synced,n,rms_z,peak_z,roll_rms,pitch_rms");
  write_line("#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts,"
             "ble_q_drops,ble_sent,ble_fails,serial_drops,imu_recoveries,inferences,"
             "inf_skips,lat_last_us,lat_max_us");
  write_line("#PROB,n,p_pothole,p_bump,p_none,lat_us");
  write_line("#DET,n,class,conf");
  write_line("#SELFTEST,result,max_diff");
  write_line("#FLT,code,name");
  write_line("#BLE,what,value");
  write_line("#CFG,<the BOOT fields again, every cfg_ms>");
}

/**
 * Every CFG_PERIOD_MS, prints the BOOT settings again as a CFG line. BOOT goes
 * out once, right after a reset, usually before the PC has the port open
 * again (a reset re-enumerates native USB), so the PC often never sees it; a
 * CFG reaches it within CFG_PERIOD_MS. A CFG is not a reboot: the GUI uses
 * one only while it has no BOOT. Like any line, it is dropped (and counted)
 * if it does not fit in the TX buffer.
 * @param st  This task's counters and the time of the last CFG.
 * @return None.
 */
static void cfg_if_due(ser_stats_t *st) {
  const int64_t now_us = esp_timer_get_time();
  if (now_us - st->last_cfg_us < (int64_t)CFG_PERIOD_MS * 1000) {
    return;
  }
  st->last_cfg_us = now_us;
  if (!print_settings("CFG")) {
    st->serial_drops++;
  }
}

/**
 * Prints one out_q item as one line.
 * @param m  Item from out_q.
 * @return true if the line was written (or the tag is unknown), false if it
 *         was dropped.
 */
static bool print_msg(const out_msg_t *m) {
  switch (m->tag) {
    case OUT_L:
      // Every float at L_DECIMALS. ok as 1/0.
      return write_line("L,%" PRIu32 ",%" PRId64 ",%.*f,%.*f,%.*f,%.*f,%.*f,%.*f,%.*f,%.*f,%.*f,%d",
                        m->l.s.n, m->l.s.t_us,
                        L_DECIMALS, m->l.s.ax, L_DECIMALS, m->l.s.ay,
                        L_DECIMALS, m->l.s.az, L_DECIMALS, m->l.s.gx,
                        L_DECIMALS, m->l.s.gy, L_DECIMALS, m->l.s.gz,
                        L_DECIMALS, m->l.z_hp, L_DECIMALS, m->l.roll,
                        L_DECIMALS, m->l.pitch, m->l.ok ? 1 : 0);
    case OUT_SEG:
      // synced as 1/0. t_start / t_end are phone epoch ms when synced is 1.
      return write_line("SEG,%" PRId64 ",%" PRId64 ",%d,%" PRIu32 ",%.3f,%.3f,%.3f,%.3f",
                        m->seg.t_start_ms, m->seg.t_end_ms, m->seg.synced ? 1 : 0,
                        m->seg.n, m->seg.rms_z, m->seg.peak_z, m->seg.roll_rms,
                        m->seg.pitch_rms);
    case OUT_HLT:
      return write_line("HLT,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
                        ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
                        ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32,
                        m->hlt.t_ms, m->hlt.samples, m->hlt.sample_q_drops,
                        m->hlt.out_q_drops, m->hlt.drdy_timeouts, m->hlt.ble_q_drops,
                        m->hlt.ble_sent, m->hlt.ble_fails, m->hlt.serial_drops,
                        m->hlt.imu_recoveries, m->hlt.inferences, m->hlt.inf_skips,
                        m->hlt.lat_last_us, m->hlt.lat_max_us);
    case OUT_PROB:
      // Probabilities at 4 decimals in model_data.h class order (P6).
      return write_line("PROB,%" PRIu32 ",%.4f,%.4f,%.4f,%" PRIu32, m->prob.n,
                        m->prob.p[0], m->prob.p[1], m->prob.p[2], m->prob.lat_us);
    case OUT_DET:
      return write_line("DET,%" PRIu32 ",%s,%.4f", m->det.n,
                        inference_class_name(m->det.cls), m->det.conf);
    case OUT_FLT:
      return write_line("FLT,F%u,%s", (unsigned)m->flt.code, fault_name(m->flt.code));
    case OUT_BLE:
      return write_line("BLE,%s,%" PRId64, link_name(m->link.what), m->link.value);
    default:
      return true;  // Unknown tag: print nothing, not a drop
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
    sys_heartbeat(ctx->sys_q, &hb);
  }
}

/**
 * Every PERF_PERIOD_MS, prints one "#LPERF" comment line (owner, 2026-11-22):
 * #LPERF,t_ms,lines,avg_us,max_us,heap_min. lines is the L lines handled in
 * the period (written or dropped), avg_us and max_us their average and
 * longest format-and-write time, and heap_min the lowest free heap since
 * boot, in bytes. lines x avg_us / period is this task's share of core 0
 * spent on L lines. Parsers skip '#' lines. Like any line, it is dropped (and
 * counted) if it does not fit in the TX buffer.
 * @param st  This task's counters; the period's L line figures are reset.
 * @return None.
 */
static void perf_if_due(ser_stats_t *st) {
  const int64_t now_us = esp_timer_get_time();
  if (now_us - st->perf_start_us < (int64_t)PERF_PERIOD_MS * 1000) {
    return;
  }
  const uint32_t avg_us = (st->l_lines > 0) ? (uint32_t)(st->l_sum_us / st->l_lines) : 0;
  if (!write_line("#LPERF,%" PRId64 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32,
                  now_us / 1000, st->l_lines, avg_us, st->l_max_us,
                  (uint32_t)esp_get_minimum_free_heap_size())) {
    st->serial_drops++;
  }
  st->perf_start_us = now_us;
  st->l_lines = 0;
  st->l_sum_us = 0;
  st->l_max_us = 0;
}

/**
 * Handles one out_q item: it becomes one line. L lines are timed for #LPERF.
 * @param m   Item from out_q.
 * @param st  This task's counters, updated.
 * @return None.
 */
static void handle_msg(const out_msg_t *m, ser_stats_t *st) {
  if (m->tag == OUT_L) {
    // Timed for #LPERF: formatting nine floats is the main cost on core 0.
    const int64_t t0_us = esp_timer_get_time();
    const bool written = print_msg(m);
    const uint32_t took_us = (uint32_t)(esp_timer_get_time() - t0_us);
    st->l_lines++;
    st->l_sum_us += took_us;
    if (took_us > st->l_max_us) {
      st->l_max_us = took_us;
    }
    if (!written) {
      st->serial_drops++;
    }
    return;
  }
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
  st.last_cfg_us = st.last_hb_us;  // The first CFG goes out CFG_PERIOD_MS after BOOT
  st.perf_start_us = st.last_hb_us;
  for (;;) {
    out_msg_t m;
    // The timeout lets the heartbeat go out even when out_q stays empty.
    if (xQueueReceive(ctx->out_q, &m, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      handle_msg(&m, &st);
    }
    heartbeat_if_due(ctx, &st);
    cfg_if_due(&st);
    perf_if_due(&st);
  }
}
