/*
 * File:        inference.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-25
 * Version:     0.2.0-bench
 * Change:      0.2.0-bench (ble-event-link-handoff.md, request H; owner,
 *              2026-11-25): every DET also queues one evt for ble_link (B1 to
 *              B5). New: two rings beside the sample ring (each sample's phone
 *              time and synced flag, from dsp), the model-class to evt-class
 *              map, the peak scan of the copied window, the evt send to ble_q
 *              (zero wait, drops counted in ble_q_drops of the heartbeat).
 *              inference_add_sample() has two more arguments. PROB, DET, the
 *              rule for a DET and the lockout are unchanged.
 *              0.1.5-bench (training-live-inference-handoff.md, request F):
 *              new file.
 * Adapted from: the model load, schema check and op resolver of
 *              examples/hello_world/main/main_functions.cc in
 *              espressif/esp-tflite-micro v1.3.5 (Apache-2.0; see
 *              firmware/cnn/libcheck/LICENSE-tflite-micro.txt), as checked on
 *              the board in request D (firmware/cnn/libcheck). Rewritten here:
 *              everything else.
 * Description: Live inference (request F). The library is the TFLite Micro
 *              copy inside esp32 core 3.3.10 (esp-tflite-micro 1.3.5, D1): no
 *              library is installed. The model is float32 (P2) and does its
 *              own scaling and downsampling (P1), so the window goes in raw.
 *
 *              Data path. dsp calls inference_add_sample() for every sample.
 *              It writes z_hp, roll and pitch into a ring of MODEL_WINDOW +
 *              MODEL_HOP samples and counts consecutive good samples (ok, no
 *              gap in n, same epoch). Every MODEL_HOP samples, if that count
 *              has reached MODEL_WINDOW (F5) and the last inference is done, it
 *              notes where the window ends and wakes the inference task with
 *              a task notification; if the task is still busy the hop is
 *              skipped and counted (inf_skips). dsp never waits.
 *
 *              No mutex. The ring is MODEL_HOP samples longer than the window,
 *              so dsp writes into the window being copied only after another
 *              MODEL_HOP samples (0.5 s), and the copy takes microseconds. The
 *              task checks the write counter after copying; a window that was
 *              overwritten anyway is dropped and counted as a skip. The busy
 *              flag is set by dsp at the handoff and cleared by the task when
 *              it is done, so there is only ever one window in flight.
 *
 *              Inference task: core 1, priority below imu_acq and dsp (F1),
 *              static stack and a static TENSOR_ARENA_BYTES arena in internal
 *              RAM (F2), a MicroMutableOpResolver with exactly the ten ops
 *              request D checked. Started first by setup() (inference_start)
 *              so the model loads and self-tests before any other task
 *              exists: TFLite Micro writes its error text straight to the
 *              console, and this way it can never mix with Serial lines.
 *              For each window it copies the samples oldest first into the
 *              input tensor, runs Invoke(), and sends PROB to out_q. If the
 *              top class (the first one on a tie) is not NONE, has at least
 *              DET_CONF, and DET_LOCKOUT_MS has passed since the last DET
 *              (counted in samples), it also sends DET. train.py's streaming
 *              test uses the same rule.
 *
 *              evt (request H). On a DET, from the window just copied: the
 *              sample with the largest |z_hp| gives n_peak and, from the ring
 *              dsp filled, the phone time t_ms and synced flag it had in its
 *              own segment (B1, CR-4); peak_z, roll_peak and pitch_peak are
 *              the largest magnitude of each channel in the window (B2);
 *              conf is the picked class's probability (B5). The evt_t goes to
 *              ble_q at once with zero wait (B3); a full queue drops it and
 *              counts it. No JSON here: ble_link formats it on core 0. A model
 *              class with no contract name (not POTHOLE or SPEED_BUMP) still
 *              prints DET but sends no evt (B4: unknown is never sent).
 *
 *              Model faults (F5 schema, F6 arena, F7 Invoke, F8 self-test)
 *              stop inference only (owner, 2026-11-24): the task stays alive,
 *              sends heartbeats carrying the fault, and dsp stops handing over
 *              windows. Sampling, the L stream and BLE go on. A live Invoke()
 *              error is the only time TFLite Micro may print while other tasks
 *              print, so its text could break one line on USB.
 */

// ============================================================================
// Includes
// ============================================================================

#include <atomic>
#include <math.h>
#include <string.h>
#include "esp_arduino_version.h"
#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "supervisor.h"
#include "inference.h"
#include "model_data.h"  // Generated by firmware/cnn/training/train.py; included here only
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "tensorflow/lite/schema/schema_utils.h"

// ============================================================================
// Macros
// ============================================================================

// The TFLite Micro library is the one inside this core (D1): another core
// version would bring a different, untested one.
#if ESP_ARDUINO_VERSION != ESP_ARDUINO_VERSION_VAL(3, 3, 10)
#error "inference.cpp uses the TFLite Micro copy inside esp32 core 3.3.10; install core 3.3.10"
#endif

// model_data.h must match the firmware. Retrain with train.py if one fails.
static_assert(MODEL_RATE_HZ == IMU_ODR_HZ, "model_data.h rate differs from IMU_ODR_HZ");
static_assert(MODEL_CLASS_COUNT == OUT_PROB_CLASSES, "model_data.h needs 3 classes");
static_assert(MODEL_CHANNEL_COUNT == 3, "model_data.h needs 3 channels (z_hp, roll, pitch)");
static_assert(MODEL_HOP > 0 && MODEL_HOP <= MODEL_WINDOW, "model_data.h hop out of range");
static_assert(MODEL_TEST_N >= 1, "model_data.h has no self-test windows");

#define RING_LEN          (MODEL_WINDOW + MODEL_HOP)  // Samples in the ring
#define OP_COUNT          10    // Ops registered (request D's list, cnn_config.TFLM_ALLOWED_OPS)
#define LOCKOUT_SAMPLES   ((uint32_t)((uint64_t)DET_LOCKOUT_MS * IMU_ODR_HZ / 1000))  // 832
#define CHANNELS_EXPECTED "z_hp,roll,pitch"   // The order dsp writes the ring in
#define NONE_NAME         "NONE"
#define POTHOLE_NAME      "POTHOLE"      // Model class names that have a contract class
#define SPEED_BUMP_NAME   "SPEED_BUMP"   // (docs/data-contract.md 4.2)
#define CLASSES_TEXT_MAX  64    // "POTHOLE|SPEED_BUMP|NONE" and room to spare

// ============================================================================
// Globals: types
// ============================================================================

// The task's own counters.
typedef struct {
  uint32_t inferences;     // Invoke() calls on live windows
  uint32_t lat_last_us;
  uint32_t lat_max_us;
  uint32_t out_q_drops;    // PROB / DET lines out_q had no room for
  uint32_t ble_q_drops;    // evt messages ble_q had no room for
  bool     have_det;      // false until the first DET
  uint32_t last_det_n;     // n of the last DET
  int64_t  last_hb_us;
} infer_stats_t;

// ============================================================================
// Globals
// ============================================================================

// Tensor arena: static, internal RAM, 16-byte aligned as TFLite Micro wants.
alignas(16) static uint8_t s_arena[TENSOR_ARENA_BYTES];

// Ring of the model channels. Written only by dsp (inference_add_sample);
// read only by the inference task, in the window dsp handed over.
static float s_ring[RING_LEN][MODEL_CHANNEL_COUNT];

// Beside the sample ring, same slots, same writer and reader rules: each
// sample's time as dsp had it when it filtered the sample (phone epoch ms if
// synced, else local ms), and whether that is phone time. ~9 KB static.
static int64_t s_ring_ms[RING_LEN];
static bool    s_ring_synced[RING_LEN];

// Shared between dsp (core 1, writer) and the inference task. Atomics, so
// neither side ever waits.
static std::atomic<uint32_t> s_written{0};   // Samples written to the ring since boot
static std::atomic<bool> s_busy{false};      // A window is handed over and not done yet
static std::atomic<bool> s_running{false};   // Model loaded and no fault: hand over windows
static std::atomic<uint32_t> s_skips{0};     // inf_skips
static std::atomic<uint8_t> s_fault{FAULT_NONE};

// The window in flight: set by dsp before the notification, read by the task
// after it (the notification orders them). Valid while s_busy is true.
static uint32_t s_hop_slot = 0;      // Ring slot one past the newest sample
static uint32_t s_hop_written = 0;   // s_written at the handoff
static uint32_t s_hop_n = 0;         // n of the newest sample

// dsp-only state (inference_add_sample runs only in dsp).
static uint32_t s_wr = 0;            // Next ring slot to write
static uint32_t s_good_run = 0;      // Consecutive good samples, capped at MODEL_WINDOW
static uint32_t s_prev_n = 0;
static uint32_t s_prev_epoch = 0;
static bool     s_have_prev = false;
static uint32_t s_hop_count = 0;     // Samples since the last hop

// The task, its static stack, and the ready signal for setup().
static TaskHandle_t s_task = NULL;
static StaticTask_t s_tcb;
static StackType_t s_stack[INFER_STACK_BYTES];   // ESP32 FreeRTOS stacks are in bytes
static SemaphoreHandle_t s_ready = NULL;
static StaticSemaphore_t s_ready_buf;

// Model facts for BOOT / CFG, and the TFLite Micro objects.
static inference_boot_t s_boot = {};
static char s_classes_text[CLASSES_TEXT_MAX];
static uint8_t s_none_index = 0;
static evt_class_t s_evt_class[MODEL_CLASS_COUNT];  // Contract class per model class, set in
                                                    // load_model(); EVT_UNKNOWN sends no evt
static tflite::MicroMutableOpResolver<OP_COUNT> s_resolver;
static tflite::MicroInterpreter *s_interp = NULL;

// ============================================================================
// Prototypes
// ============================================================================

static void inference_task(void *param);
static fault_code_t load_model(void);
static fault_code_t self_test(void);
static bool shape_is(const TfLiteTensor *t, int d0, int d1, int d2, int dims);
static void run_window(const app_ctx_t *ctx, infer_stats_t *st);
static void stop_inference(fault_code_t code);
static void send_out(QueueHandle_t out_q, const out_msg_t *m, uint32_t *drops);
static void heartbeat_if_due(const app_ctx_t *ctx, infer_stats_t *st);

// ============================================================================
// Implementations: called from other tasks
// ============================================================================

void inference_start(app_ctx_t *ctx) {
  s_boot.model_version = MODEL_VERSION;
  s_boot.classes = s_classes_text;
  s_boot.window = MODEL_WINDOW;
  s_boot.hop = MODEL_HOP;
  s_ready = xSemaphoreCreateBinaryStatic(&s_ready_buf);
  // Higher priority than the Arduino loop task that runs setup() on the same
  // core, so it starts loading at once.
  s_task = xTaskCreateStaticPinnedToCore(inference_task, "inference", INFER_STACK_BYTES, ctx,
                                         PRIO_INFERENCE, s_stack, &s_tcb, CORE_INFERENCE);
  configASSERT(s_task != NULL);
  // Not ready in time means the task hung: setup goes on, and the missing
  // heartbeats become F3 like any other stuck task.
  (void)xSemaphoreTake(s_ready, pdMS_TO_TICKS(INFER_READY_TIMEOUT_MS));
}

void inference_add_sample(uint32_t n, uint32_t epoch, float z_hp, float roll, float pitch,
                          bool ok, int64_t t_ms, bool synced) {
  s_ring[s_wr][0] = z_hp;
  s_ring[s_wr][1] = roll;
  s_ring[s_wr][2] = pitch;
  s_ring_ms[s_wr] = t_ms;
  s_ring_synced[s_wr] = synced;
  s_wr = (s_wr + 1) % RING_LEN;
  s_written.store(s_written.load(std::memory_order_relaxed) + 1, std::memory_order_release);

  // Consecutive good samples: an ok=0 row, a jump in n (samples lost on the
  // way) or a new epoch (IMU recovered) starts the count again (F5).
  const bool follows = s_have_prev && epoch == s_prev_epoch && n == s_prev_n + 1;
  if (!ok) {
    s_good_run = 0;
  } else if (!follows) {
    s_good_run = 1;
  } else if (s_good_run < MODEL_WINDOW) {
    s_good_run++;
  }
  s_have_prev = true;
  s_prev_n = n;
  s_prev_epoch = epoch;

  if (++s_hop_count < MODEL_HOP) {
    return;
  }
  s_hop_count = 0;
  if (!s_running.load(std::memory_order_acquire) || s_good_run < MODEL_WINDOW) {
    return;  // Not loaded, faulted, or no full good window yet: no inference, no skip
  }
  if (s_busy.load(std::memory_order_acquire)) {
    s_skips.fetch_add(1, std::memory_order_relaxed);  // Still on the last window
    return;
  }
  s_hop_slot = s_wr;
  s_hop_written = s_written.load(std::memory_order_relaxed);
  s_hop_n = n;
  s_busy.store(true, std::memory_order_release);
  xTaskNotify(s_task, n, eSetValueWithOverwrite);  // Never waits
}

const inference_boot_t *inference_boot(void) {
  return &s_boot;
}

fault_code_t inference_fault(void) {
  return (fault_code_t)s_fault.load(std::memory_order_acquire);
}

const char *inference_class_name(uint8_t k) {
  return (k < MODEL_CLASS_COUNT) ? g_model_class_names[k] : "?";
}

// ============================================================================
// Implementations: the inference task
// ============================================================================

/**
 * Checks one tensor's shape.
 * @param t     Tensor.
 * @param d0    Expected first dimension.
 * @param d1    Second.
 * @param d2    Third (ignored when dims is 2).
 * @param dims  Expected number of dimensions (2 or 3).
 * @return true if it is float32 with exactly that shape.
 */
static bool shape_is(const TfLiteTensor *t, int d0, int d1, int d2, int dims) {
  if (t == NULL || t->type != kTfLiteFloat32 || t->dims == NULL || t->dims->size != dims) {
    return false;
  }
  return t->dims->data[0] == d0 && t->dims->data[1] == d1 &&
         (dims == 2 || t->dims->data[2] == d2);
}

/**
 * Loads model_data.h: schema version, channel order, class names, every op
 * registered, the tensor arena, and the input and output shapes.
 * @return FAULT_NONE, FAULT_MODEL_SCHEMA or FAULT_ARENA.
 */
static fault_code_t load_model(void) {
  // Class names, '|'-separated for BOOT (commas separate BOOT's fields).
  size_t used = 0;
  bool have_none = false;
  for (uint8_t k = 0; k < MODEL_CLASS_COUNT; k++) {
    const int w = snprintf(s_classes_text + used, sizeof(s_classes_text) - used, "%s%s",
                           k ? "|" : "", g_model_class_names[k]);
    if (w > 0) {
      used += (size_t)w;
    }
    if (used >= sizeof(s_classes_text)) {
      used = sizeof(s_classes_text) - 1;
    }
    if (strcmp(g_model_class_names[k], NONE_NAME) == 0) {
      s_none_index = k;
      have_none = true;
    }
    // Which contract class this model class is (B4); anything else sends no evt.
    if (strcmp(g_model_class_names[k], POTHOLE_NAME) == 0) {
      s_evt_class[k] = EVT_POTHOLE;
    } else if (strcmp(g_model_class_names[k], SPEED_BUMP_NAME) == 0) {
      s_evt_class[k] = EVT_BUMP;
    } else {
      s_evt_class[k] = EVT_UNKNOWN;
    }
  }
  if (!have_none || strcmp(MODEL_CHANNELS, CHANNELS_EXPECTED) != 0) {
    return FAULT_MODEL_SCHEMA;
  }

  const tflite::Model *model = tflite::GetModel(g_model_data);
  if (model->version() != TFLITE_SCHEMA_VERSION) {
    return FAULT_MODEL_SCHEMA;
  }
  // Exactly the ops request D checked on this board. Any other op in the model
  // is a schema fault here rather than a confusing allocation failure later.
  if (s_resolver.AddConv2D() != kTfLiteOk || s_resolver.AddMaxPool2D() != kTfLiteOk ||
      s_resolver.AddAveragePool2D() != kTfLiteOk || s_resolver.AddMean() != kTfLiteOk ||
      s_resolver.AddFullyConnected() != kTfLiteOk || s_resolver.AddSoftmax() != kTfLiteOk ||
      s_resolver.AddReshape() != kTfLiteOk || s_resolver.AddExpandDims() != kTfLiteOk ||
      s_resolver.AddSub() != kTfLiteOk || s_resolver.AddMul() != kTfLiteOk) {
    return FAULT_MODEL_SCHEMA;
  }
  const auto *codes = model->operator_codes();
  if (codes == NULL) {
    return FAULT_MODEL_SCHEMA;
  }
  for (uint32_t i = 0; i < codes->size(); i++) {
    if (s_resolver.FindOp(tflite::GetBuiltinCode(codes->Get(i))) == NULL) {
      return FAULT_MODEL_SCHEMA;
    }
  }

  // Constructed once, on first use, in this task.
  static tflite::MicroInterpreter interp(model, s_resolver, s_arena, TENSOR_ARENA_BYTES);
  if (interp.AllocateTensors() != kTfLiteOk) {
    return FAULT_ARENA;
  }
  s_interp = &interp;
  s_boot.arena_used = (uint32_t)interp.arena_used_bytes();
  if (interp.inputs_size() != 1 || interp.outputs_size() != 1 ||
      !shape_is(interp.input(0), 1, MODEL_WINDOW, MODEL_CHANNEL_COUNT, 3) ||
      !shape_is(interp.output(0), 1, MODEL_CLASS_COUNT, 0, 2)) {
    return FAULT_MODEL_SCHEMA;
  }
  return FAULT_NONE;
}

/**
 * Boot self-test (F6): runs model_data.h's MODEL_TEST_N windows and compares
 * every output with the Keras output stored beside it.
 * @return FAULT_NONE, FAULT_INVOKE or FAULT_SELFTEST.
 */
static fault_code_t self_test(void) {
  float worst = 0.0f;
  for (int i = 0; i < MODEL_TEST_N; i++) {
    memcpy(s_interp->input(0)->data.f, g_model_test_x[i], sizeof(g_model_test_x[i]));
    if (s_interp->Invoke() != kTfLiteOk) {
      return FAULT_INVOKE;
    }
    const float *p = s_interp->output(0)->data.f;
    for (int k = 0; k < MODEL_CLASS_COUNT; k++) {
      const float d = fabsf(p[k] - g_model_test_p[i][k]);
      if (!(d <= worst)) {
        worst = d;  // Also catches NaN, which never passes
      }
    }
  }
  s_boot.selftest_ran = true;
  s_boot.selftest_diff = worst;
  s_boot.selftest_pass = (worst <= SELFTEST_TOL);
  return s_boot.selftest_pass ? FAULT_NONE : FAULT_SELFTEST;
}

/**
 * Stops inference for good (until reset): dsp hands over no more windows, and
 * the heartbeat carries the fault so the supervisor reports it.
 * @param code  The model fault.
 * @return None.
 */
static void stop_inference(fault_code_t code) {
  s_running.store(false, std::memory_order_release);
  s_fault.store((uint8_t)code, std::memory_order_release);
}

/**
 * Sends one line to out_q without waiting. A full queue drops it.
 * @param out_q  The out_q handle.
 * @param m      Line to send.
 * @param drops  This task's drop counter.
 * @return None.
 */
static void send_out(QueueHandle_t out_q, const out_msg_t *m, uint32_t *drops) {
  if (xQueueSend(out_q, m, 0) != pdTRUE) {
    (*drops)++;
  }
}

/**
 * Runs the model on the window dsp handed over, then sends PROB (and, on a
 * detection, DET and its evt). Clears the busy flag when done.
 * @param ctx  For out_q.
 * @param st   This task's counters.
 * @return None.
 */
static void run_window(const app_ctx_t *ctx, infer_stats_t *st) {
  const uint32_t end_slot = s_hop_slot;
  const uint32_t end_written = s_hop_written;
  const uint32_t n = s_hop_n;

  // Oldest first: at most two pieces, around the end of the ring.
  float *dst = s_interp->input(0)->data.f;
  const uint32_t start = (end_slot + RING_LEN - MODEL_WINDOW) % RING_LEN;
  const uint32_t first = (RING_LEN - start < MODEL_WINDOW) ? RING_LEN - start : MODEL_WINDOW;
  memcpy(dst, s_ring[start], first * sizeof(s_ring[0]));
  memcpy(dst + first * MODEL_CHANNEL_COUNT, s_ring[0], (MODEL_WINDOW - first) * sizeof(s_ring[0]));

  // Peaks of the window (B1, B2), found on the copy and before Invoke(), which
  // may reuse the input buffer. Magnitudes, as before the overhaul. The sample
  // with the largest |z_hp| is the event's peak (the first one on a tie). Its
  // time comes from the ring slot it was copied from.
  float peak_z = 0.0f;
  float peak_roll = 0.0f;
  float peak_pitch = 0.0f;
  uint32_t peak_i = 0;  // Index in the window, oldest = 0
  for (uint32_t i = 0; i < MODEL_WINDOW; i++) {
    const float *row = dst + i * MODEL_CHANNEL_COUNT;  // z_hp, roll, pitch
    const float az = fabsf(row[0]);
    if (az > peak_z) {
      peak_z = az;
      peak_i = i;
    }
    peak_roll = fmaxf(peak_roll, fabsf(row[1]));
    peak_pitch = fmaxf(peak_pitch, fabsf(row[2]));
  }
  const uint32_t peak_slot = (start + peak_i) % RING_LEN;
  const int64_t peak_t_ms = s_ring_ms[peak_slot];
  const bool peak_synced = s_ring_synced[peak_slot];

  // dsp overwrites the window's oldest sample MODEL_HOP + 1 samples after its
  // end. If that happened during the copy or the peak time read, drop the window.
  if (s_written.load(std::memory_order_acquire) - end_written > MODEL_HOP) {
    s_skips.fetch_add(1, std::memory_order_relaxed);
    s_busy.store(false, std::memory_order_release);
    return;
  }

  const int64_t t0_us = esp_timer_get_time();
  const TfLiteStatus status = s_interp->Invoke();
  const uint32_t lat_us = (uint32_t)(esp_timer_get_time() - t0_us);
  if (status != kTfLiteOk) {
    stop_inference(FAULT_INVOKE);
    s_busy.store(false, std::memory_order_release);
    return;
  }
  st->inferences++;
  st->lat_last_us = lat_us;
  if (lat_us > st->lat_max_us) {
    st->lat_max_us = lat_us;
  }

  const float *p = s_interp->output(0)->data.f;
  out_msg_t m = {};
  m.tag = OUT_PROB;
  m.prob.n = n;
  uint8_t top = 0;
  for (uint8_t k = 0; k < MODEL_CLASS_COUNT; k++) {
    m.prob.p[k] = p[k];
    if (p[k] > p[top]) {
      top = k;  // Strictly greater: a tie goes to the first class, as argmax
    }
  }
  m.prob.lat_us = lat_us;
  send_out(ctx->out_q, &m, &st->out_q_drops);

  // The board's rule (O1): top class not NONE, at least DET_CONF, and at least
  // DET_LOCKOUT_MS since the last DET (unsigned difference: safe across wrap).
  if (top != s_none_index && p[top] >= DET_CONF &&
      (!st->have_det || n - st->last_det_n >= LOCKOUT_SAMPLES)) {
    out_msg_t d = {};
    d.tag = OUT_DET;
    d.det.n = n;
    d.det.cls = top;
    d.det.conf = p[top];
    send_out(ctx->out_q, &d, &st->out_q_drops);
    st->have_det = true;
    st->last_det_n = n;

    // The same detection goes to the app as an evt (B3): a plain struct to
    // ble_q, never waiting. The window has no gaps (dsp only hands over runs of
    // consecutive n), so the peak's n follows from its index. A class with no
    // contract name sends nothing (B4).
    if (s_evt_class[top] != EVT_UNKNOWN) {
      ble_msg_t b = {};
      b.tag = BLE_EVT;
      b.evt.t_ms = peak_t_ms;
      b.evt.n_peak = n - (MODEL_WINDOW - 1) + peak_i;
      b.evt.conf = p[top];
      b.evt.peak_z_g = peak_z;
      b.evt.peak_roll_dps = peak_roll;
      b.evt.peak_pitch_dps = peak_pitch;
      b.evt.synced = peak_synced;
      b.evt.cls = s_evt_class[top];
      if (xQueueSend(ctx->ble_q, &b, 0) != pdTRUE) {
        st->ble_q_drops++;
      }
    }
  }
  s_busy.store(false, std::memory_order_release);
}

/**
 * Sends this task's heartbeat, with its counters and model fault, at most
 * once per HEARTBEAT_PERIOD_MS. Goes on after a model fault, so a stopped
 * inference is never mistaken for a stuck task (F3).
 * @param ctx  For sys_q.
 * @param st   This task's counters.
 * @return None.
 */
static void heartbeat_if_due(const app_ctx_t *ctx, infer_stats_t *st) {
  const int64_t now_us = esp_timer_get_time();
  if (now_us - st->last_hb_us < (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
    return;
  }
  st->last_hb_us = now_us;
  sys_msg_t hb = {};
  hb.task = TASK_INFERENCE;
  hb.fault = inference_fault();
  hb.out_q_drops = st->out_q_drops;
  hb.ble_q_drops = st->ble_q_drops;
  hb.inferences = st->inferences;
  hb.inf_skips = s_skips.load(std::memory_order_relaxed);
  hb.lat_last_us = st->lat_last_us;
  hb.lat_max_us = st->lat_max_us;
  sys_heartbeat(ctx->sys_q, &hb);
}

/**
 * Inference task: load and self-test, tell setup() it may go on, then one
 * window per notification. After a model fault it only sends heartbeats.
 * @param param  Pointer to the app_ctx_t from setup().
 * @return Never returns.
 */
static void inference_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;
  fault_code_t f = load_model();
  if (f == FAULT_NONE) {
    f = self_test();
  }
  if (f == FAULT_NONE) {
    s_running.store(true, std::memory_order_release);
  } else {
    stop_inference(f);
  }
  xSemaphoreGive(s_ready);

  infer_stats_t st = {};
  st.last_hb_us = esp_timer_get_time();
  for (;;) {
    uint32_t value = 0;
    // The timeout lets the heartbeat go out when no window comes (settling,
    // a fault, or a stopped stream).
    if (xTaskNotifyWait(0, UINT32_MAX, &value, pdMS_TO_TICKS(HEARTBEAT_PERIOD_MS)) == pdTRUE &&
        s_running.load(std::memory_order_acquire)) {
      run_window(ctx, &st);
    }
    heartbeat_if_due(ctx, &st);
  }
}
