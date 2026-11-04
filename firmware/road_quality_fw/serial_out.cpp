/*
 * File:        serial_out.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-02
 * Version:     0.1.0-draft (first draft)
 * Description: serial_out task (core 0, above supervisor). Sole owner of
 *              Serial. Calls Serial.begin, prints BOOT and the column headers,
 *              then turns each out_q item into one line. Line format (DRAFT):
 *              comma-separated, no spaces, one record per line, each starting
 *              with a type tag. Lines starting with '#' are comments a parser
 *              skips. A slow Serial write only ever blocks this task; dsp and
 *              supervisor see a full out_q and count drops.
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

#define LINE_MAX_CHARS  192  // Longest line (BOOT) is ~170 characters

// ============================================================================
// Prototypes
// ============================================================================

static void write_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static const char *class_name(evt_class_t c);
static const char *fault_name(fault_code_t f);
static void print_boot(void);
static void print_msg(const out_msg_t *m);

// ============================================================================
// Implementations
// ============================================================================

/**
 * Formats one line and writes it with a trailing newline. A line longer than
 * the buffer is cut short rather than overflowing.
 * @param fmt  printf-style format, without the newline.
 * @param ...  Format arguments.
 * @return None.
 */
static void write_line(const char *fmt, ...) {
  char line[LINE_MAX_CHARS];
  va_list args;
  va_start(args, fmt);
  int len = vsnprintf(line, sizeof(line) - 1, fmt, args);  // Room for '\n'
  va_end(args);
  if (len < 0) {
    return;  // Encoding error: nothing sensible to print
  }
  if (len > (int)sizeof(line) - 2) {
    len = (int)sizeof(line) - 2;  // Truncated: vsnprintf kept what fit
  }
  line[len++] = '\n';
  Serial.write((const uint8_t *)line, (size_t)len);
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
    default:              return "NONE";
  }
}

/**
 * Prints BOOT with every setting in use, so each capture records how it was
 * run, then a '#' header naming the columns of each line type.
 * @return None.
 */
static void print_boot(void) {
  write_line("BOOT,fw=%s,odr=%d,acc_fs=%d,gyr_fs=%d,thr_z=%.3f,thr_roll=%.1f,"
             "thr_pitch=%.1f,lockout_ms=%d,hpf_hz=%.2f,print_every=%d,"
             "settle_ms=%d,dominance=%.2f",
             FW_VERSION, IMU_ODR_HZ, ACCEL_FS_G, GYRO_FS_DPS, THR_Z_G,
             THR_ROLL_DPS, THR_PITCH_DPS, LOCKOUT_MS, HPF_CUTOFF_HZ,
             PRINT_EVERY_N, SETTLE_MS, DOMINANCE_RATIO);
  write_line("#S,n,t_us,ax,ay,az,gx,gy,gz,z_hp");
  write_line("#EVT,seq,n_peak,t_us_peak,class,peak_z_g,peak_roll_dps,peak_pitch_dps");
  write_line("#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts");
  write_line("#FLT,code,name");
}

/**
 * Prints one out_q item as one line.
 * @param m  Item from out_q.
 * @return None.
 */
static void print_msg(const out_msg_t *m) {
  switch (m->tag) {
    case OUT_S:
      // 4 decimals for g (0.49 mg/LSB at +-16 g), 2 for dps (17.5 mdps/LSB).
      write_line("S,%" PRIu32 ",%" PRId64 ",%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.4f",
                 m->s.s.n, m->s.s.t_us, m->s.s.ax, m->s.s.ay, m->s.s.az,
                 m->s.s.gx, m->s.s.gy, m->s.s.gz, m->s.z_hp);
      break;
    case OUT_EVT:
      write_line("EVT,%" PRIu32 ",%" PRIu32 ",%" PRId64 ",%s,%.3f,%.1f,%.1f",
                 m->evt.seq, m->evt.n_peak, m->evt.t_us_peak,
                 class_name(m->evt.cls), m->evt.peak_z_g,
                 m->evt.peak_roll_dps, m->evt.peak_pitch_dps);
      break;
    case OUT_HLT:
      write_line("HLT,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32,
                 m->hlt.t_ms, m->hlt.samples, m->hlt.sample_q_drops,
                 m->hlt.out_q_drops, m->hlt.drdy_timeouts);
      break;
    case OUT_FLT:
      write_line("FLT,F%u,%s", (unsigned)m->flt.code, fault_name(m->flt.code));
      break;
    default:
      break;  // Unknown tag: print nothing
  }
}

void serial_out_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;

  // UART0 via the USB-to-UART bridge (USB CDC On Boot disabled). No wait for
  // a host: the board must run with no monitor attached.
  Serial.begin(SERIAL_BAUD);
  print_boot();

  int64_t last_hb_us = esp_timer_get_time();
  for (;;) {
    out_msg_t m;
    // The timeout lets the heartbeat go out even when out_q stays empty.
    if (xQueueReceive(ctx->out_q, &m, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      print_msg(&m);
    }

    const int64_t now_us = esp_timer_get_time();
    if (now_us - last_hb_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
      last_hb_us = now_us;
      sys_heartbeat(ctx->sys_q, TASK_SERIAL_OUT, 0, 0, 0);
    }
  }
}
