/*
 * File:        dsp.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-23
 * Version:     0.1.4-bench
 * Change:      0.1.4-bench (manual-recording-overhaul-handoff.md, request A;
 *              owner decision O2): no detection. The thresholds, lockout
 *              window, re-arm rule, pitch / vertical lobe trackers, classifier,
 *              EVT (Serial and BLE), snapshots and S lines are removed (git tag
 *              pre-manual-overhaul). Every sample goes to out_q as an L line;
 *              segments go on as before. map_axes(), the HPF, the gyro bias
 *              and the settle time are unchanged, so z_hp, roll and pitch are
 *              exactly what 0.1.3-bench sent. dsp_axis_map() gives BOOT the
 *              mapping text.
 *              0.1.3-bench: log build (L lines); bench mapping with chip Y
 *              forward (roll = chip Y gyro, pitch = chip X gyro).
 *              0.1.2-bench: re-arm rule, record-mode snapshots.
 *              0.1.1-bench: classification by pitch sequence; roll and pitch
 *              bias measured over the settle time; filters restart on a new
 *              sample epoch (IMU recovered).
 *              0.2.0-draft: BLE. 1 s segment summaries (contract 4.1) and
 *              phone time (contract section 2, CR-4).
 * Description: dsp task (core 1, below imu_acq). For every sample from
 *              sample_q: map chip axes to car axes, remove the roll and pitch
 *              gyro bias (measured over the settle time), high-pass the
 *              vertical acceleration, add the sample to the current 1 s
 *              segment, and send it to out_q as an L line (raw chip axes, the
 *              model channels z_hp / roll / pitch, and ok = bias ready). Each
 *              finished segment goes to out_q (Serial) and ble_q (app). Never
 *              waits: a full queue drops the item and counts it.
 */

// ============================================================================
// Includes
// ============================================================================

#include <math.h>
#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "dsp.h"
#include "supervisor.h"

// ============================================================================
// Globals: types
// ============================================================================

// One sample in car axes, only the three model axes.
typedef struct {
  float vert_g;     // Vertical acceleration, g (gravity still in)
  float roll_dps;   // Roll rate: side-to-side rocking
  float pitch_dps;  // Pitch rate: nose up / down
} car_axes_t;

// State of the first-order IIR high-pass filter.
typedef struct {
  float a;          // Coefficient RC / (RC + dt)
  float x_prev;     // x[n-1]
  float y_prev;     // y[n-1]
  bool  seeded;     // false until the first sample
} hpf_t;

// Offset from local time to phone time (contract section 2).
typedef struct {
  bool    synced;     // false until the first TIME write is applied
  int64_t offset_ms;  // phone_ms - local_ms
} phone_clock_t;

// Roll and pitch rate bias: the mean over the settle time.
typedef struct {
  bool     ready;         // false until the settle time has passed
  uint32_t n;             // Samples summed so far
  float    sum_roll;
  float    sum_pitch;
  float    roll;          // Bias in dps, subtracted once ready
  float    pitch;
} bias_t;

// Running sums for one 1 s segment.
typedef struct {
  bool     open;          // false before the first sample
  int64_t  index;         // Window number: local time / SEGMENT_MS
  int64_t  t_first_us;    // First sample time
  int64_t  t_last_us;     // Last sample time so far
  uint32_t n;             // Samples so far
  float    sum_z2;        // Sums of squares for the RMS values
  float    sum_roll2;
  float    sum_pitch2;
  float    peak_z;        // Largest |z_hp| so far, g
} seg_acc_t;

// ============================================================================
// Prototypes
// ============================================================================

static car_axes_t map_axes(const imu_sample_t *s);
static float hpf_step(hpf_t *f, float x);
static int64_t to_phone_ms(const phone_clock_t *c, int64_t t_us);
static void seg_add(seg_acc_t *g, const imu_sample_t *s, float z_hp,
                    const car_axes_t *car);
static seg_t seg_finish(const seg_acc_t *g, const phone_clock_t *c);
static void send_out(QueueHandle_t out_q, const out_msg_t *m, uint32_t *drops);
static void send_ble(QueueHandle_t ble_q, const ble_msg_t *m, uint32_t *drops);

// ============================================================================
// Implementations
// ============================================================================

/**
 * The only place chip axes become car axes. Bench mapping (DRAFT) until
 * Kyle's mounting info: chip Y points forward and chip Z up, so chip X points
 * to the right. Vertical = chip Z accel, roll = chip Y gyro (rotation about
 * the forward axis), pitch = chip X gyro (rotation about the side axis).
 * Signs stay as the chip gives them (positive chip X rate is nose up).
 * dsp_axis_map() below must say the same.
 * @param s  Sample in chip axes.
 * @return The three model axes in car axes.
 */
static car_axes_t map_axes(const imu_sample_t *s) {
  car_axes_t c;
  c.vert_g = s->az;
  c.roll_dps = s->gy;
  c.pitch_dps = s->gx;
  return c;
}

const char *dsp_axis_map(void) {
  // Must match map_axes() just above: car axis = chip axis.
  return "vert=az,roll=gy,pitch=gx";
}

/**
 * One step of a first-order IIR high-pass filter:
 *   y[n] = a * (y[n-1] + x[n] - x[n-1]),  a = RC / (RC + dt)
 * Source: https://en.wikipedia.org/wiki/High-pass_filter#Discrete-time_realization
 * The first call seeds x[n-1] with x, so boot does not start with a 1 g step
 * (the output starts at 0 instead of 1 g decaying away).
 * @param f  Filter state, updated.
 * @param x  New input sample.
 * @return Filtered output y[n].
 */
static float hpf_step(hpf_t *f, float x) {
  if (!f->seeded) {
    f->x_prev = x;
    f->y_prev = 0.0f;
    f->seeded = true;
  }
  const float y = f->a * (f->y_prev + x - f->x_prev);
  f->x_prev = x;
  f->y_prev = y;
  return y;
}

/**
 * Converts a local sample time to the time the app expects. esp_timer and
 * Arduino's millis() are the same clock, so local ms = t_us / 1000.
 * @param c     Offset in use.
 * @param t_us  Local time, microseconds since boot.
 * @return Phone epoch ms if synced, otherwise local ms since boot.
 */
static int64_t to_phone_ms(const phone_clock_t *c, int64_t t_us) {
  return t_us / 1000 + (c->synced ? c->offset_ms : 0);
}

/**
 * Adds one sample to the open segment.
 * @param g     Open segment, updated.
 * @param s     Current sample (for t_us).
 * @param z_hp  High-passed vertical acceleration, g.
 * @param car   Roll and pitch rates, dps.
 * @return None.
 */
static void seg_add(seg_acc_t *g, const imu_sample_t *s, float z_hp,
                    const car_axes_t *car) {
  g->t_last_us = s->t_us;
  g->n++;
  g->sum_z2 += z_hp * z_hp;
  g->sum_roll2 += car->roll_dps * car->roll_dps;
  g->sum_pitch2 += car->pitch_dps * car->pitch_dps;
  g->peak_z = fmaxf(g->peak_z, fabsf(z_hp));
}

/**
 * Turns a finished segment's sums into the values the contract sends.
 * @param g  Finished segment, with n >= 1.
 * @param c  Offset in use for this whole segment.
 * @return The segment summary.
 */
static seg_t seg_finish(const seg_acc_t *g, const phone_clock_t *c) {
  seg_t out;
  out.t_start_ms = to_phone_ms(c, g->t_first_us);
  out.t_end_ms = to_phone_ms(c, g->t_last_us);
  out.synced = c->synced;
  out.n = g->n;
  out.rms_z = sqrtf(g->sum_z2 / (float)g->n);
  out.peak_z = g->peak_z;
  out.roll_rms = sqrtf(g->sum_roll2 / (float)g->n);
  out.pitch_rms = sqrtf(g->sum_pitch2 / (float)g->n);
  return out;
}

/**
 * Sends one line to out_q without waiting. A full queue drops the line.
 * @param out_q  The out_q handle.
 * @param m      Line to send.
 * @param drops  This task's out_q drop counter, incremented on a drop.
 * @return None.
 */
static void send_out(QueueHandle_t out_q, const out_msg_t *m, uint32_t *drops) {
  if (xQueueSend(out_q, m, 0) != pdTRUE) {
    (*drops)++;
  }
}

/**
 * Sends one segment to ble_q without waiting. A full queue drops it.
 * ble_link decides whether a phone is there to receive it.
 * @param ble_q  The ble_q handle.
 * @param m      Item to send.
 * @param drops  This task's ble_q drop counter, incremented on a drop.
 * @return None.
 */
static void send_ble(QueueHandle_t ble_q, const ble_msg_t *m, uint32_t *drops) {
  if (xQueueSend(ble_q, m, 0) != pdTRUE) {
    (*drops)++;
  }
}

void dsp_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;

  // Filter coefficient from the cutoff and the sample period, so changing
  // HPF_CUTOFF_HZ or IMU_ODR_HZ in config.h is enough.
  const float dt = 1.0f / (float)IMU_ODR_HZ;
  const float rc = 1.0f / (2.0f * (float)PI * HPF_CUTOFF_HZ);
  hpf_t hpf = {};
  hpf.a = rc / (rc + dt);

  phone_clock_t phone = {};    // Offset in use for the current segment
  seg_acc_t seg = {};
  uint32_t out_q_drops = 0;
  uint32_t ble_q_drops = 0;
  bias_t bias = {};
  bool have_first = false;
  uint32_t epoch = 0;          // Sample epoch in use (imu_acq bumps it per recovery)
  int64_t settle_end_us = 0;   // The gyro bias is measured until this sample time
  int64_t last_hb_us = esp_timer_get_time();

  for (;;) {
    imu_sample_t s;
    // The timeout lets the heartbeat go out even when no samples arrive.
    if (xQueueReceive(ctx->sample_q, &s, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      if (!have_first || s.epoch != epoch) {
        // Boot, or the IMU came back after F1/F2: its data restarts from
        // scratch, so do the filter and the bias (L lines carry ok = 0
        // until the new bias is ready). The open segment closes as usual on
        // this sample, since an outage outlasts its 1 s window.
        have_first = true;
        epoch = s.epoch;
        settle_end_us = s.t_us + (int64_t)SETTLE_MS * 1000;
        hpf.seeded = false;
        bias = bias_t{};
      }

      car_axes_t car = map_axes(&s);
      if (!bias.ready) {
        if (s.t_us < settle_end_us) {
          bias.sum_roll += car.roll_dps;
          bias.sum_pitch += car.pitch_dps;
          bias.n++;
        } else {
          // A mean beyond GYRO_BIAS_MAX_DPS means the board moved during
          // settle; then no bias is better than a wrong one.
          if (bias.n > 0) {
            const float r = bias.sum_roll / (float)bias.n;
            const float p = bias.sum_pitch / (float)bias.n;
            bias.roll = (fabsf(r) <= GYRO_BIAS_MAX_DPS) ? r : 0.0f;
            bias.pitch = (fabsf(p) <= GYRO_BIAS_MAX_DPS) ? p : 0.0f;
          }
          bias.ready = true;
        }
      }
      if (bias.ready) {
        car.roll_dps -= bias.roll;
        car.pitch_dps -= bias.pitch;
      }
      const float z_hp = hpf_step(&hpf, car.vert_g);

      // Segments: the first sample of a new 1 s window of local time closes
      // the previous one. Windows are fixed to local time, so n is 416 or 417
      // with nothing lost (contract 4.1).
      const int64_t index = s.t_us / ((int64_t)SEGMENT_MS * 1000);
      if (seg.open && index != seg.index) {
        const seg_t done = seg_finish(&seg, &phone);
        out_msg_t o = {};
        o.tag = OUT_SEG;
        o.seg = done;
        send_out(ctx->out_q, &o, &out_q_drops);
        ble_msg_t b = {};
        b.tag = BLE_SEG;
        b.seg = done;
        send_ble(ctx->ble_q, &b, &ble_q_drops);
        seg.open = false;
      }
      if (!seg.open) {
        // A new offset takes effect only here, between segments, so one
        // segment never mixes two offsets (CR-4). time_q holds only the
        // latest TIME write; reading it empties it.
        time_sync_t ts;
        if (xQueueReceive(ctx->time_q, &ts, 0) == pdTRUE) {
          phone.offset_ms = ts.phone_ms - ts.local_ms;
          phone.synced = true;
        }
        seg = seg_acc_t{};
        seg.open = true;
        seg.index = index;
        seg.t_first_us = s.t_us;
      }
      seg_add(&seg, &s, z_hp, &car);

      // Every sample goes out as an L line, without waiting. A full out_q
      // drops it and counts out_q_drops, so every lost sample shows up as a
      // jump in n on the PC. Floats are formatted later, on core 0.
      out_msg_t m = {};
      m.tag = OUT_L;
      m.l.s = s;
      m.l.z_hp = z_hp;
      m.l.roll = car.roll_dps;
      m.l.pitch = car.pitch_dps;
      m.l.ok = bias.ready;
      send_out(ctx->out_q, &m, &out_q_drops);
    }

    const int64_t now_us = esp_timer_get_time();
    if (now_us - last_hb_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
      last_hb_us = now_us;
      sys_msg_t hb = {};
      hb.task = TASK_DSP;
      hb.out_q_drops = out_q_drops;
      hb.ble_q_drops = ble_q_drops;
      sys_heartbeat(ctx->sys_q, &hb);
    }
  }
}
