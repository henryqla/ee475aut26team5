/*
 * File:        dsp.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-16
 * Version:     0.1.1-bench
 * Change:      Classification by pitch sequence. The front axle decides: a
 *              pothole pitches the nose down then up, a speed bump up then
 *              down, so the class is the sign of the first pitch lobe of the
 *              event (smoothed, bias-corrected pitch rate, with hysteresis; a
 *              lobe that ended just before the window opened counts). No
 *              pitch lobe: roll at threshold = pothole (one wheel), else the
 *              sign of the first vertical excursion. Never UNKNOWN. Roll and
 *              pitch bias measured over the settle time. On a new sample
 *              epoch (IMU recovered) the filters, bias, trackers and settle
 *              hold-off restart and an open window is discarded.
 *              0.2.0-draft: BLE. Adds 1 s segment summaries (contract 4.1) and phone time
 *              (contract section 2, CR-4): a new offset from time_q takes
 *              effect only when a segment starts, and an event keeps the
 *              offset in use when its peak was recorded. Segments and events
 *              also go to ble_q.
 * Description: dsp task (core 1, below imu_acq). For every sample from
 *              sample_q: map chip axes to car axes, high-pass the vertical
 *              acceleration, add the sample to the current 1 s segment, and
 *              run the event detector (settle hold-off, thresholds, one shared
 *              lockout window, peak tracking, pitch / vertical lobe tracking,
 *              and classification at the end of the window). Sends every PRINT_EVERY_N-th sample as an S line,
 *              and each segment and event to both out_q (Serial) and ble_q
 *              (app), all without waiting. DRAFT detection algorithm from
 *              devkit-phase1-context.md v0.4, "Detection".
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

// One sample in car axes, only the three detection axes.
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

// Moving average over the last PITCH_SMOOTH_N values.
typedef struct {
  float    buf[PITCH_SMOOTH_N];
  float    sum;
  uint32_t idx;           // Next slot to overwrite
  uint32_t count;         // Values held, up to PITCH_SMOOTH_N
} smooth_t;

// Lobe tracker for one signed signal. A lobe starts when |v| reaches start
// and ends when v falls below end on the lobe's own side (hysteresis), or
// reverses. Signs: +1 or -1, 0 for none.
typedef struct {
  float   start;          // |v| that starts a lobe
  float   end;            // A lobe ends when sign * v drops below this
  int8_t  active;         // Sign of the lobe in progress
  int64_t active_start_us;
  int8_t  last_sign;      // Sign of the most recent finished lobe
  int64_t last_end_us;    // ... and when it ended
} lobe_track_t;

// The lobes of one event window, for one signal.
typedef struct {
  int8_t first;           // Sign of the event's first lobe, 0 if none yet
  bool   seq;             // The next lobe was opposite and within the gap
  bool   done;            // The lobe after the first has been judged
} lobe_seq_t;

// One lockout window. Peaks are in threshold units: |value| / threshold, so
// 1.0 means exactly at threshold and the axes can be compared directly.
typedef struct {
  bool     open;          // true inside a lockout window
  int64_t  end_us;        // Sample time at which the window closes
  float    peak_z_u;
  float    peak_roll_u;
  float    peak_pitch_u;
  float    peak_max_u;    // Largest of the three so far
  uint32_t n_peak;        // Sample count where peak_max_u occurred
  int64_t  t_us_peak;     // ... and its local timestamp
  int64_t  t_ms_peak;     // ... and the same instant on the phone clock
  bool     synced_peak;   // Whether t_ms_peak is phone time
  lobe_seq_t pitch;       // Pitch lobes (+1 = nose down)
  lobe_seq_t z;           // Vertical lobes of z_hp (+1 = up)
} window_t;

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
static void window_track(window_t *w, const imu_sample_t *s, float z_u,
                         float roll_u, float pitch_u, const phone_clock_t *c);
static float smooth_step(smooth_t *f, float x);
static int8_t lobe_step(lobe_track_t *k, int64_t t_us, float v);
static void lobe_seq_open(lobe_seq_t *q, const lobe_track_t *k, int64_t t_us);
static void lobe_seq_step(lobe_seq_t *q, const lobe_track_t *k, int8_t started,
                          int64_t t_us);
static evt_class_t classify(const window_t *w, evt_basis_t *basis);
static void send_out(QueueHandle_t out_q, const out_msg_t *m, uint32_t *drops);
static void send_ble(QueueHandle_t ble_q, const ble_msg_t *m, uint32_t *drops);

// ============================================================================
// Implementations
// ============================================================================

/**
 * The only place chip axes become car axes. Bench mapping (DRAFT) until
 * Kyle's mounting info: vertical = chip Z accel, roll = chip X gyro,
 * pitch = chip Y gyro.
 * @param s  Sample in chip axes.
 * @return The three detection axes in car axes.
 */
static car_axes_t map_axes(const imu_sample_t *s) {
  car_axes_t c;
  c.vert_g = s->az;
  c.roll_dps = s->gx;
  c.pitch_dps = s->gy;
  return c;
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
 * Updates the open window's per-axis peaks and, if this sample holds the
 * largest peak of any axis so far, records its sample count and time. The
 * phone time is fixed here, with the offset in use now, so the event keeps
 * its own segment's offset even if it is sent after the next one (CR-4).
 * @param w        Open window, updated.
 * @param s        Current sample (for n and t_us).
 * @param z_u      |z_hp| in threshold units.
 * @param roll_u   |roll rate| in threshold units.
 * @param pitch_u  |pitch rate| in threshold units.
 * @param c        Offset in use for the current segment.
 * @return None.
 */
static void window_track(window_t *w, const imu_sample_t *s, float z_u,
                         float roll_u, float pitch_u, const phone_clock_t *c) {
  w->peak_z_u = fmaxf(w->peak_z_u, z_u);
  w->peak_roll_u = fmaxf(w->peak_roll_u, roll_u);
  w->peak_pitch_u = fmaxf(w->peak_pitch_u, pitch_u);

  const float m = fmaxf(z_u, fmaxf(roll_u, pitch_u));
  if (m > w->peak_max_u) {  // Strict: the first of equal peaks is kept
    w->peak_max_u = m;
    w->n_peak = s->n;
    w->t_us_peak = s->t_us;
    w->t_ms_peak = to_phone_ms(c, s->t_us);
    w->synced_peak = c->synced;
  }
}

/**
 * One step of a moving average over the last PITCH_SMOOTH_N inputs (fewer
 * right after a reset).
 * @param f  Filter state, updated.
 * @param x  New input.
 * @return Mean of the values held.
 */
static float smooth_step(smooth_t *f, float x) {
  if (f->count == PITCH_SMOOTH_N) {
    f->sum -= f->buf[f->idx];
  } else {
    f->count++;
  }
  f->buf[f->idx] = x;
  f->sum += x;
  f->idx = (f->idx + 1) % PITCH_SMOOTH_N;
  return f->sum / (float)f->count;
}

/**
 * Feeds one value to a lobe tracker. A lobe that reverses ends and the
 * opposite one starts on the same sample (gap 0).
 * @param k     Tracker, updated.
 * @param t_us  Sample time.
 * @param v     Signed value.
 * @return Sign of a lobe that started on this sample, or 0.
 */
static int8_t lobe_step(lobe_track_t *k, int64_t t_us, float v) {
  if (k->active != 0 && (float)k->active * v < k->end) {
    k->last_sign = k->active;
    k->last_end_us = t_us;
    k->active = 0;
  }
  if (k->active == 0 && fabsf(v) >= k->start) {
    k->active = (v > 0.0f) ? 1 : -1;
    k->active_start_us = t_us;
    return k->active;
  }
  return 0;
}

/**
 * Sets a new window's first lobe. The window opens on a threshold crossing,
 * which can come partway into the event: a small nose-down lobe can end
 * before the larger nose-up lobe that opens the window. So a lobe that ended
 * at most PITCH_LOBE_MAX_GAP_MS before the current lobe started (or before
 * now, if none is active) leads the event, and the current lobe is judged as
 * its follower. Only one lobe back is considered.
 * @param q     The window's lobe record, written.
 * @param k     Tracker, already stepped with this sample.
 * @param t_us  Sample time the window opened.
 * @return None.
 */
static void lobe_seq_open(lobe_seq_t *q, const lobe_track_t *k, int64_t t_us) {
  *q = lobe_seq_t{};
  const int64_t ref_us = (k->active != 0) ? k->active_start_us : t_us;
  const bool lead = k->last_sign != 0 &&
                    ref_us - k->last_end_us <= (int64_t)PITCH_LOBE_MAX_GAP_MS * 1000;
  if (lead) {
    q->first = k->last_sign;
    if (k->active != 0) {
      q->seq = (k->active == -q->first);
      q->done = true;
    }
  } else if (k->active != 0) {
    q->first = k->active;
  }
}

/**
 * Records a lobe that started inside an open window: the first lobe if none
 * yet, else judges the next one against it (opposite sign, starting at most
 * PITCH_LOBE_MAX_GAP_MS after the first one ended).
 * @param q        The window's lobe record, updated.
 * @param k        Tracker, already stepped with this sample.
 * @param started  lobe_step()'s return for this sample.
 * @param t_us     Sample time.
 * @return None.
 */
static void lobe_seq_step(lobe_seq_t *q, const lobe_track_t *k, int8_t started,
                          int64_t t_us) {
  if (started == 0) {
    return;
  }
  if (q->first == 0) {
    q->first = started;
  } else if (!q->done) {
    // last_end_us is the first lobe's end: nothing else has ended since.
    q->seq = (started == -q->first) &&
             (t_us - k->last_end_us <= (int64_t)PITCH_LOBE_MAX_GAP_MS * 1000);
    q->done = true;
  }
}

/**
 * Classifies a finished window. Always POTHOLE or BUMP, in this order:
 * 1. A pitch lobe was seen: nose down first = pothole (the front wheels drop
 *    in, then climb out), nose up first = speed bump. The opposite lobe
 *    following (pitch.seq) confirms it but is not required.
 * 2. No pitch lobe, roll reached its threshold: pothole (one wheel).
 * 3. Neither: first vertical excursion down = pothole (the car drops), up =
 *    speed bump. No excursion at all also gives pothole.
 * @param w      Finished window.
 * @param basis  Receives which rule decided.
 * @return EVT_POTHOLE or EVT_BUMP.
 */
static evt_class_t classify(const window_t *w, evt_basis_t *basis) {
  if (w->pitch.first != 0) {
    *basis = BASIS_PITCH;
    return (w->pitch.first > 0) ? EVT_POTHOLE : EVT_BUMP;
  }
  if (w->peak_roll_u >= 1.0f) {
    *basis = BASIS_ROLL;
    return EVT_POTHOLE;
  }
  *basis = BASIS_Z;
  return (w->z.first > 0) ? EVT_BUMP : EVT_POTHOLE;
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
 * Sends one segment or event to ble_q without waiting. A full queue drops it.
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
  window_t win = {};
  uint32_t seq = 0;            // Next event number. 0 at every boot (CR-6)
  uint32_t out_q_drops = 0;
  uint32_t ble_q_drops = 0;
  bias_t bias = {};
  smooth_t pitch_smooth = {};
  // Trackers: thresholds set here, state cleared on every (re)start.
  const lobe_track_t pitch_idle = {PITCH_LOBE_DPS, PITCH_LOBE_END_DPS, 0, 0, 0, 0};
  const lobe_track_t z_idle = {Z_LOBE_G, 0.5f * Z_LOBE_G, 0, 0, 0, 0};
  lobe_track_t pitch_trk = pitch_idle;  // Input: nose-down rate, dps
  lobe_track_t z_trk = z_idle;          // Input: z_hp, g
  bool have_first = false;
  uint32_t epoch = 0;          // Sample epoch in use (imu_acq bumps it per recovery)
  int64_t settle_end_us = 0;   // No detection before this sample time
  int64_t last_hb_us = esp_timer_get_time();

  for (;;) {
    imu_sample_t s;
    // The timeout lets the heartbeat go out even when no samples arrive.
    if (xQueueReceive(ctx->sample_q, &s, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE) {
      if (!have_first || s.epoch != epoch) {
        // Boot, or the IMU came back after F1/F2: its data restarts from
        // scratch, so does everything that remembers past samples. An open
        // window would mix pre-loss data (maybe garbage) with new data, so it
        // is dropped without an event. The open segment closes as usual on
        // this sample, since an outage outlasts its 1 s window.
        have_first = true;
        epoch = s.epoch;
        settle_end_us = s.t_us + (int64_t)SETTLE_MS * 1000;
        hpf.seeded = false;
        bias = bias_t{};
        pitch_smooth = smooth_t{};
        pitch_trk = pitch_idle;
        z_trk = z_idle;
        win = window_t{};
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

      // Lobe trackers run from the end of settle, inside and outside
      // windows, so a lobe just before a window opens is still known.
      int8_t pitch_started = 0;
      int8_t z_started = 0;
      if (bias.ready) {
        const float nose_down_dps =
            smooth_step(&pitch_smooth, (float)PITCH_NOSE_DOWN_SIGN * car.pitch_dps);
        pitch_started = lobe_step(&pitch_trk, s.t_us, nose_down_dps);
        z_started = lobe_step(&z_trk, s.t_us, z_hp);
      }

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

      // Events 1. Close the window once a sample lands past its end, and emit
      //    the event with the peak's n and time (so it goes out up to
      //    LOCKOUT_MS after its peak, as CR-5 allows).
      if (win.open && s.t_us >= win.end_us) {
        evt_t e = {};
        e.seq = seq++;  // Counts dropped events too, so a seq gap shows a drop
        e.n_peak = win.n_peak;
        e.t_us_peak = win.t_us_peak;
        e.t_ms = win.t_ms_peak;
        e.synced = win.synced_peak;
        e.cls = classify(&win, &e.basis);
        e.peak_z_g = win.peak_z_u * THR_Z_G;
        e.peak_roll_dps = win.peak_roll_u * THR_ROLL_DPS;
        e.peak_pitch_dps = win.peak_pitch_u * THR_PITCH_DPS;
        e.pitch_first = win.pitch.first;
        e.pitch_seq = win.pitch.seq;

        out_msg_t o = {};
        o.tag = OUT_EVT;
        o.evt = e;
        send_out(ctx->out_q, &o, &out_q_drops);
        ble_msg_t b = {};
        b.tag = BLE_EVT;
        b.evt = e;
        send_ble(ctx->ble_q, &b, &ble_q_drops);
        win.open = false;
      }

      // Events 2. Open a window when any axis reaches its threshold, after the
      //    settle hold-off and outside any window. One window for all axes.
      //    The first lobes come from the trackers, looking one lobe back.
      bool opened = false;
      if (!win.open && s.t_us >= settle_end_us &&
          (fabsf(z_hp) >= THR_Z_G ||
           fabsf(car.roll_dps) >= THR_ROLL_DPS ||
           fabsf(car.pitch_dps) >= THR_PITCH_DPS)) {
        win = window_t{};
        win.open = true;
        win.end_us = s.t_us + (int64_t)LOCKOUT_MS * 1000;
        lobe_seq_open(&win.pitch, &pitch_trk, s.t_us);
        lobe_seq_open(&win.z, &z_trk, s.t_us);
        opened = true;
      }

      // Events 3. Inside a window, track peaks (starting with the trigger
      //    sample) and lobes (after it: lobe_seq_open already saw this one).
      if (win.open) {
        window_track(&win, &s, fabsf(z_hp) / THR_Z_G,
                     fabsf(car.roll_dps) / THR_ROLL_DPS,
                     fabsf(car.pitch_dps) / THR_PITCH_DPS, &phone);
        if (!opened) {
          lobe_seq_step(&win.pitch, &pitch_trk, pitch_started, s.t_us);
          lobe_seq_step(&win.z, &z_trk, z_started, s.t_us);
        }
      }

      // Every PRINT_EVERY_N-th sample also goes out as an S line.
      if (s.n % PRINT_EVERY_N == 0) {
        out_msg_t m = {};
        m.tag = OUT_S;
        m.s.s = s;
        m.s.z_hp = z_hp;
        send_out(ctx->out_q, &m, &out_q_drops);
      }
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
