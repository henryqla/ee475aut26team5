/*
 * File:        serial_out.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      BLE. New SEG and BLE lines. EVT gains t_ms and synced. HLT
 *              gains ble_q_drops, ble_sent, ble_fails, serial_drops. FLT knows
 *              F4. BOOT adds seg_ms and ble. PCB target: Serial is native USB,
 *              and a line that does not fit in the USB TX buffer is dropped
 *              and counted instead of waiting.
 * Description: serial_out task (core 0, same priority as ble_link). Sole owner
 *              of Serial, which is the S3's native USB (USB Serial/JTAG); the
 *              PCB has no UART bridge. Calls Serial.begin, prints BOOT and the
 *              column headers, then turns each out_q item into one line. Line
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
#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "serial_out.h"
#include "supervisor.h"

// ============================================================================
// Macros
// ============================================================================

#define LINE_MAX_CHARS  224  // Longest line (BOOT) is ~190 characters

// The PCB build needs Serial on native USB in Hardware CDC mode: the board has
// no UART bridge, so with other settings nothing would ever print. These come
// from Tools > USB CDC On Boot and Tools > USB Mode.
#if !ARDUINO_USB_CDC_ON_BOOT || !ARDUINO_USB_MODE
#error "PCB build: set Tools > USB CDC On Boot: Enabled and Tools > USB Mode: Hardware CDC and JTAG"
#endif

// ============================================================================
// Prototypes
// ============================================================================

static bool write_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static const char *class_name(evt_class_t c);
static const char *fault_name(fault_code_t f);
static const char *link_name(link_event_t w);
static void print_boot(void);
static bool print_msg(const out_msg_t *m);

// ============================================================================
// Implementations
// ============================================================================

/**
 * Formats one line and writes it with a trailing newline, but only if the
 * whole line fits in the USB TX buffer right now. Never waits. A line longer
 * than the format buffer is cut short rather than overflowing.
 * @param fmt  printf-style format, without the newline.
 * @param ...  Format arguments.
 * @return true if the line was written, false if it was dropped.
 */
static bool write_line(const char *fmt, ...) {
  char line[LINE_MAX_CHARS];
  va_list args;
  va_start(args, fmt);
  int len = vsnprintf(line, sizeof(line) - 1, fmt, args);  // Room for '\n'
  va_end(args);
  if (len < 0) {
    return false;  // Encoding error: nothing sensible to print
  }
  if (len > (int)sizeof(line) - 2) {
    len = (int)sizeof(line) - 2;  // Truncated: vsnprintf kept what fit
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
             "thr_pitch=%.1f,lockout_ms=%d,hpf_hz=%.2f,print_every=%d,"
             "settle_ms=%d,dominance=%.2f,seg_ms=%d,ble=%s",
             FW_VERSION, IMU_ODR_HZ, ACCEL_FS_G, GYRO_FS_DPS, THR_Z_G,
             THR_ROLL_DPS, THR_PITCH_DPS, LOCKOUT_MS, HPF_CUTOFF_HZ,
             PRINT_EVERY_N, SETTLE_MS, DOMINANCE_RATIO, SEGMENT_MS,
             BLE_DEVICE_NAME);
  write_line("#S,n,t_us,ax,ay,az,gx,gy,gz,z_hp");
  write_line("#SEG,t_start,t_end,synced,n,rms_z,peak_z,roll_rms,pitch_rms");
  write_line("#EVT,seq,n_peak,t_us_peak,class,peak_z_g,peak_roll_dps,peak_pitch_dps,t_ms,synced");
  write_line("#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts,"
             "ble_q_drops,ble_sent,ble_fails,serial_drops");
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
      return write_line("EVT,%" PRIu32 ",%" PRIu32 ",%" PRId64 ",%s,%.3f,%.1f,%.1f,%" PRId64 ",%d",
                        m->evt.seq, m->evt.n_peak, m->evt.t_us_peak,
                        class_name(m->evt.cls), m->evt.peak_z_g,
                        m->evt.peak_roll_dps, m->evt.peak_pitch_dps, m->evt.t_ms,
                        m->evt.synced ? 1 : 0);
    case OUT_HLT:
      return write_line("HLT,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32
                        ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32,
                        m->hlt.t_ms, m->hlt.samples, m->hlt.sample_q_drops,
                        m->hlt.out_q_drops, m->hlt.drdy_timeouts, m->hlt.ble_q_drops,
                        m->hlt.ble_sent, m->hlt.ble_fails, m->hlt.serial_drops);
    case OUT_FLT:
      return write_line("FLT,F%u,%s", (unsigned)m->flt.code, fault_name(m->flt.code));
    case OUT_BLE:
      return write_line("BLE,%s,%" PRId64, link_name(m->link.what), m->link.value);
    default:
      return true;  // Unknown tag: print nothing, not a drop
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

  uint32_t serial_drops = 0;
  int64_t last_hb_us = esp_timer_get_time();
  for (;;) {
    out_msg_t m;
    // The timeout lets the heartbeat go out even when out_q stays empty.
    if (xQueueReceive(ctx->out_q, &m, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      if (!print_msg(&m)) {
        serial_drops++;
      }
    }

    const int64_t now_us = esp_timer_get_time();
    if (now_us - last_hb_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
      last_hb_us = now_us;
      sys_msg_t hb = {};
      hb.task = TASK_SERIAL_OUT;
      hb.serial_drops = serial_drops;
      sys_heartbeat(ctx->sys_q, &hb);
    }
  }
}
