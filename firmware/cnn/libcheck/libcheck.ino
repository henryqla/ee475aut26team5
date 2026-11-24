/*
 * File:        libcheck.ino
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-24
 * Version:     0.1.0
 * Change:      0.1.0 (training-live-inference-handoff.md, request D): new file.
 * Description: Throwaway check that TensorFlow Lite Micro (TFLM) builds and
 *              runs on the ESP32-S3 with esp32 core 3.3.10, before train.py
 *              (request E) and live inference (request F) depend on it.
 *
 *              Library (D1, owner 2026-11-24): the copy of Espressif's
 *              esp-tflite-micro 1.3.5, with ESP-NN 1.2.3, that core 3.3.10
 *              already ships precompiled for the S3 (Arduino15/packages/esp32/
 *              tools/esp32s3-libs/3.3.10). Nothing is installed: every S3
 *              sketch already has its headers on the include path and its
 *              library on the link line, and the core defines
 *              TF_LITE_STATIC_MEMORY for sketches, as the library was built.
 *              ESP-NN speeds up int8 kernels only; float32 models (P2) run
 *              TFLM's reference kernels.
 *
 *              What it checks, once at boot and again whenever any key is sent
 *              from the Serial Monitor:
 *              1. LIB-3: registers all ten ops request E's model may use in one
 *                 MicroMutableOpResolver and prints each result. That resolver
 *                 never runs a model.
 *              2. LIB-2, for two copies of TFLM's hello_world sine model: int8
 *                 (the library's own example) and float32 (upstream TFLM; the
 *                 float path is the one request F runs). Each runs with a
 *                 resolver holding only FULLY_CONNECTED, all it needs, on a
 *                 static arena. The golden inputs of upstream's
 *                 hello_world_test.cc must give |sin(x) - y| <= 0.05. Then 100
 *                 timed Invoke() calls over the example's 20-point sweep give
 *                 the mean, min and max time per run. The arena bytes actually
 *                 used are printed.
 *              3. LIB-4, for each model: a second interpreter on an arena short
 *                 by min(1 KB, half the bytes used) (owner, option b) must fail
 *                 AllocateTensors() and say so. The board keeps running: a SUM
 *                 line every SUM_PERIOD_MS shows it did not crash or reboot.
 *              TFLM's own error text goes to the ESP-IDF console: the DevKit's
 *              "UART" connector, and also USB (the core's secondary console),
 *              between this sketch's lines. DebugLog is built into the core's
 *              library and cannot be redirected.
 *
 *              With LIBCHECK_BASELINE 1, the same sketch builds without TFLM
 *              (Serial, the task and the SUM line only). The difference
 *              between the two Verify outputs is what TFLM and the two models
 *              add in flash and RAM.
 *
 *              Serial is used only on core 0: setup() starts one task there and
 *              deletes its own task, as firmware-bench does.
 *
 *              Build: the same board settings as firmware-bench (Arduino IDE
 *              2.3.10, esp32 core 3.3.10, "ESP32S3 Dev Module", Flash Size 8MB,
 *              PSRAM Disabled, USB CDC On Boot Enabled, USB Mode "Hardware CDC
 *              and JTAG"). No libraries. See README.md.
 *
 *              Adapted from (Apache-2.0, LICENSE-tflite-micro.txt):
 *              - espressif/esp-tflite-micro v1.3.5,
 *                examples/hello_world/main/main_functions.cc: model load,
 *                schema check, resolver, int8 quantize / dequantize, the
 *                20-point sweep over 0..2*pi;
 *              - tensorflow/tflite-micro commit 8efacb0,
 *                tensorflow/lite/micro/examples/hello_world/hello_world_test.cc:
 *                the golden inputs and the 0.05 tolerance.
 *              Rewritten for this check: timing, arena reports, the op list,
 *              the short-arena test and the output lines.
 */

// ============================================================================
// Includes
// ============================================================================

#include <inttypes.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include "esp_arduino_version.h"
#include "esp_idf_version.h"
#include "esp_system.h"
#include "esp_timer.h"

// ============================================================================
// Macros
// ============================================================================

// 1: build without TFLM, to measure what TFLM adds (compare the two Verify
// outputs). From arduino-cli it can also be set without editing:
//   --build-property "compiler.cpp.extra_flags=-DLIBCHECK_BASELINE=1"
#ifndef LIBCHECK_BASELINE
#define LIBCHECK_BASELINE   0
#endif

#define LIBCHECK_VERSION    "0.1.0"
#define TFLM_VERSION        "1.3.5"  // esp-tflite-micro in esp32 core 3.3.10 (versions.txt)
#define ESP_NN_VERSION      "1.2.3"  // espressif__esp-nn in the same file

#define SERIAL_BAUD         115200   // Ignored by native USB
#define TX_BUF_BYTES        8192     // Holds one full report (~4 KB). The boot report is
                                     // usually lost anyway before a PC opens the port (it
                                     // was on the first run), so send any key to re-run
#define LINE_MAX_CHARS      256      // Longest line is ~130 characters
#define TASK_STACK_BYTES    8192     // DRAFT. Free stack is printed in every SUM line
#define TASK_PRIO           2        // Above Arduino's loopTask (1); nothing else runs
#define TASK_CORE           0        // Serial only on core 0 (CLAUDE.md)
#define POLL_MS             50       // How often the task checks for a key
#define SUM_PERIOD_MS       5000     // One SUM line this often

#define ARENA_BYTES         8192     // DRAFT. The examples use 2000 (int8) and 3000 (float)
#define SHORT_MAX_BYTES     1024     // LIB-4: arena short by min(this, used / 2) (option b)
#define ALL_OPS_N           10       // Ops on request E's DRAFT list (request D, step 2)

#define TIMED_RUNS          100      // Invoke() calls timed per model
#define SWEEP_POINTS        20       // kInferencesPerCycle in the example
#define SWEEP_RANGE         (2.0f * 3.14159265359f)  // kXrange in the example
#define GOLD_N              4        // Golden inputs per model (hello_world_test.cc)
#define GOLD_TOL            0.05f    // Its epsilon: |sin(x) - y| <= GOLD_TOL

// Same check as firmware-bench: Serial must be native USB in Hardware CDC mode.
#if !ARDUINO_USB_CDC_ON_BOOT || !ARDUINO_USB_MODE
#error "libcheck: set Tools > USB CDC On Boot: Enabled and Tools > USB Mode: Hardware CDC and JTAG"
#endif

// The library under test is the one inside core 3.3.10, so another core
// version would test a different TFLM.
#if ESP_ARDUINO_VERSION != ESP_ARDUINO_VERSION_VAL(3, 3, 10)
#error "libcheck: checks the TFLM copy inside esp32 core 3.3.10; install core 3.3.10"
#endif

#if !LIBCHECK_BASELINE
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "hello_world_models.h"
#endif

// ============================================================================
// Globals: types
// ============================================================================

#if !LIBCHECK_BASELINE
// One model to check.
typedef struct {
  const char          *name;    // "int8" or "float"
  const unsigned char *data;    // TFLite flatbuffer
  int                  len;     // Its length, bytes
  TfLiteType           type;    // Input and output type the model must have
  const float         *gold_x;  // GOLD_N golden inputs
} model_spec_t;

// What one model's checks found, kept for the SUM line.
typedef struct {
  bool     ran;          // false until checked once
  bool     load_ok;      // Schema version, resolver and tensor types as expected
  bool     alloc_ok;     // AllocateTensors() on the full arena
  bool     gold_ok;      // Every golden input within GOLD_TOL
  bool     short_ok;     // AllocateTensors() failed on the short arena, as it should
  uint32_t invoke_fails; // Invoke() calls that did not return kTfLiteOk
  size_t   arena_used;   // arena_used_bytes() after AllocateTensors()
  float    mean_us;      // Mean Invoke() time over TIMED_RUNS
  uint32_t max_us;       // Longest Invoke() time
} model_result_t;
#endif

// ============================================================================
// Globals
// ============================================================================

static uint32_t s_runs = 0;  // Times the checks have run since boot

#if !LIBCHECK_BASELINE
// TFLM requires a 16-byte aligned arena. Static, so it is counted in the
// Verify output's RAM figure, as request F's arena will be.
alignas(16) static uint8_t s_arena[ARENA_BYTES];

// Golden inputs from hello_world_test.cc. The int8 test feeds these through
// the input's quantization; the float test feeds them directly.
static const float kGoldInt8[GOLD_N] = {0.77f, 1.57f, 2.3f, 3.14f};
static const float kGoldFloat[GOLD_N] = {0.0f, 1.0f, 3.0f, 5.0f};

static const model_spec_t kModels[] = {
  {"int8", g_hello_world_int8_model, g_hello_world_int8_model_len, kTfLiteInt8, kGoldInt8},
  {"float", g_hello_world_float_model, g_hello_world_float_model_len, kTfLiteFloat32,
   kGoldFloat},
};
#define MODEL_COUNT  (sizeof(kModels) / sizeof(kModels[0]))

static model_result_t s_results[MODEL_COUNT];
static int s_ops_ok = 0;  // Ops registered in the last run (of ALL_OPS_N)
#endif

// ============================================================================
// Prototypes
// ============================================================================

static void out_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void run_checks(void);
static void print_sum(void);
static void libcheck_task(void *param);
#if !LIBCHECK_BASELINE
static const char *type_name(TfLiteType t);
static void set_input(TfLiteTensor *t, float x);
static float get_output(const TfLiteTensor *t);
static int check_ops(void);
static void check_model(const model_spec_t *m, model_result_t *r);
static void check_short_arena(const model_spec_t *m, const tflite::Model *model,
                              const tflite::MicroOpResolver &resolver, model_result_t *r);
static bool model_passed(const model_result_t *r);
#endif

// ============================================================================
// Implementations
// ============================================================================

/**
 * Formats one line and writes it to Serial with a trailing '\n'. Lines longer
 * than LINE_MAX_CHARS - 2 are cut short. Waits up to the core's TX timeout
 * when the USB TX buffer is full (fine here: nothing is timed while printing).
 * @param fmt  printf-style format, without the newline.
 * @param ...  Format arguments.
 * @return None.
 */
static void out_line(const char *fmt, ...) {
  // Static: only libcheck_task prints, one line at a time.
  static char line[LINE_MAX_CHARS];
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

#if !LIBCHECK_BASELINE
/**
 * Short name of a tensor type for the output lines.
 * @param t  Tensor type.
 * @return "int8", "float32" or "other".
 */
static const char *type_name(TfLiteType t) {
  if (t == kTfLiteInt8) {
    return "int8";
  }
  if (t == kTfLiteFloat32) {
    return "float32";
  }
  return "other";
}

/**
 * Writes one x value into a model's single-value input tensor, quantizing it
 * for an int8 input as the example does: q = x / scale + zero_point, rounded
 * and clamped to the int8 range.
 * @param t  Input tensor (int8 or float32, one value).
 * @param x  Input value.
 * @return None.
 */
static void set_input(TfLiteTensor *t, float x) {
  if (t->type == kTfLiteInt8) {
    long q = lroundf(x / t->params.scale) + (long)t->params.zero_point;
    if (q < -128) {
      q = -128;
    } else if (q > 127) {
      q = 127;
    }
    t->data.int8[0] = (int8_t)q;
  } else {
    t->data.f[0] = x;
  }
}

/**
 * Reads a model's single-value output tensor, dequantizing an int8 output as
 * the example does: y = (q - zero_point) * scale.
 * @param t  Output tensor (int8 or float32, one value).
 * @return The output value.
 */
static float get_output(const TfLiteTensor *t) {
  if (t->type == kTfLiteInt8) {
    return (float)((int)t->data.int8[0] - (int)t->params.zero_point) * t->params.scale;
  }
  return t->data.f[0];
}

/**
 * LIB-3: registers every op on request E's DRAFT list in one resolver and
 * prints each result. The resolver is a local, so a re-run starts empty (a
 * second Add of the same op would fail), and it never runs a model.
 * @return Ops registered (ALL_OPS_N when all of them were).
 */
static int check_ops(void) {
  tflite::MicroMutableOpResolver<ALL_OPS_N> resolver;
  // Braced lists run their calls left to right, so names and results line up.
  const TfLiteStatus st[ALL_OPS_N] = {
    resolver.AddConv2D(),       resolver.AddMaxPool2D(),  resolver.AddAveragePool2D(),
    resolver.AddMean(),         resolver.AddFullyConnected(), resolver.AddSoftmax(),
    resolver.AddReshape(),      resolver.AddExpandDims(), resolver.AddSub(),
    resolver.AddMul(),
  };
  static const char *const kNames[ALL_OPS_N] = {
    "CONV_2D", "MAX_POOL_2D", "AVERAGE_POOL_2D", "MEAN", "FULLY_CONNECTED",
    "SOFTMAX", "RESHAPE", "EXPAND_DIMS", "SUB", "MUL",
  };
  int ok = 0;
  for (int i = 0; i < ALL_OPS_N; i++) {
    out_line("OPS,%s,%s", kNames[i], (st[i] == kTfLiteOk) ? "ok" : "FAIL");
    if (st[i] == kTfLiteOk) {
      ok++;
    }
  }
  out_line("OPS,all,registered=%d,of=%d,%s", ok, ALL_OPS_N,
           (ok == ALL_OPS_N) ? "pass" : "FAIL");
  return ok;
}

/**
 * LIB-4: builds a second interpreter on an arena short by
 * min(SHORT_MAX_BYTES, used / 2) and checks that AllocateTensors() fails.
 * Never less than half of what the model used, because TFLM keeps its own
 * bookkeeping in the arena and an arena too small even for that would crash
 * inside the constructor instead of returning an error (owner, option b).
 * The SHORT line before the attempt shows where it stopped if it ever does.
 * @param m         Model being checked.
 * @param model     Its parsed flatbuffer.
 * @param resolver  The resolver the full-arena run used.
 * @param r         Results, short_ok updated.
 * @return None.
 */
static void check_short_arena(const model_spec_t *m, const tflite::Model *model,
                              const tflite::MicroOpResolver &resolver, model_result_t *r) {
  const size_t half = r->arena_used / 2;
  const size_t deficit = (half < SHORT_MAX_BYTES) ? half : SHORT_MAX_BYTES;
  const size_t short_bytes = r->arena_used - deficit;
  out_line("SHORT,%s,arena=%zu,short_by=%zu,expect=alloc_fail", m->name, short_bytes, deficit);
  tflite::MicroInterpreter interp(model, resolver, s_arena, short_bytes);
  const TfLiteStatus st = interp.AllocateTensors();
  r->short_ok = (st != kTfLiteOk);
  out_line("SHORT,%s,arena=%zu,short_by=%zu,alloc=%s,%s", m->name, short_bytes, deficit,
           r->short_ok ? "failed" : "ok", r->short_ok ? "pass" : "FAIL");
}

/**
 * LIB-2 and LIB-4 for one model: load it, allocate on the full arena, check
 * the golden inputs, time TIMED_RUNS Invoke() calls over the 20-point sweep,
 * print the sweep, then try the short arena.
 * @param m  Model to check.
 * @param r  Results, overwritten.
 * @return None.
 */
static void check_model(const model_spec_t *m, model_result_t *r) {
  *r = model_result_t{};
  r->ran = true;

  const tflite::Model *model = tflite::GetModel(m->data);
  if (model->version() != TFLITE_SCHEMA_VERSION) {
    out_line("MODEL,%s,bytes=%d,schema=%" PRIu32 ",expected=%d,FAIL", m->name, m->len,
             (uint32_t)model->version(), TFLITE_SCHEMA_VERSION);
    return;
  }
  // Only the op the example needs (request D, step 3).
  tflite::MicroMutableOpResolver<1> resolver;
  if (resolver.AddFullyConnected() != kTfLiteOk) {
    out_line("MODEL,%s,resolver=FAIL", m->name);
    return;
  }

  {
    // Scoped, so this interpreter is gone before the short-arena one reuses
    // the arena.
    tflite::MicroInterpreter interp(model, resolver, s_arena, ARENA_BYTES);
    if (interp.AllocateTensors() != kTfLiteOk) {
      out_line("MODEL,%s,bytes=%d,arena=%d,alloc=FAIL", m->name, m->len, ARENA_BYTES);
      return;
    }
    r->alloc_ok = true;
    r->arena_used = interp.arena_used_bytes();
    TfLiteTensor *in = interp.input(0);
    TfLiteTensor *outp = interp.output(0);
    r->load_ok = (in->type == m->type) && (outp->type == m->type);
    out_line("MODEL,%s,bytes=%d,schema=%" PRIu32 ",in=%s,out=%s,arena=%d,arena_used=%zu,%s",
             m->name, m->len, (uint32_t)model->version(), type_name(in->type),
             type_name(outp->type), ARENA_BYTES, r->arena_used,
             r->load_ok ? "ok" : "FAIL_wrong_type");
    if (!r->load_ok) {
      return;
    }

    // Golden inputs (hello_world_test.cc). The error is against sin(x) of the
    // nominal x, as the test does, even where int8 rounds x.
    r->gold_ok = true;
    for (int i = 0; i < GOLD_N; i++) {
      const float x = m->gold_x[i];
      set_input(in, x);
      if (interp.Invoke() != kTfLiteOk) {
        r->gold_ok = false;
        r->invoke_fails++;
        out_line("GOLD,%s,x=%.2f,invoke=FAIL", m->name, (double)x);
        continue;
      }
      const float y = get_output(outp);
      const float err = fabsf(sinf(x) - y);
      const bool pass = (err <= GOLD_TOL);
      r->gold_ok = r->gold_ok && pass;
      out_line("GOLD,%s,x=%.2f,y=%.4f,sin=%.4f,err=%.4f,tol=%.2f,%s", m->name, (double)x,
               (double)y, (double)sinf(x), (double)err, (double)GOLD_TOL,
               pass ? "pass" : "FAIL");
    }

    // Timing: only Invoke() is inside the timer. x walks the example's
    // 20-point sweep over 0..2*pi; the first cycle's outputs are kept to print.
    float sweep_y[SWEEP_POINTS];
    uint64_t sum_us = 0;
    uint32_t min_us = UINT32_MAX;
    for (int i = 0; i < TIMED_RUNS; i++) {
      const float x = (float)(i % SWEEP_POINTS) / (float)SWEEP_POINTS * SWEEP_RANGE;
      set_input(in, x);
      const int64_t t0_us = esp_timer_get_time();
      const TfLiteStatus st = interp.Invoke();
      const uint32_t took_us = (uint32_t)(esp_timer_get_time() - t0_us);
      if (st != kTfLiteOk) {
        r->invoke_fails++;
      }
      sum_us += took_us;
      if (took_us > r->max_us) {
        r->max_us = took_us;
      }
      if (took_us < min_us) {
        min_us = took_us;
      }
      if (i < SWEEP_POINTS) {
        sweep_y[i] = get_output(outp);
      }
    }
    r->mean_us = (float)sum_us / (float)TIMED_RUNS;
    out_line("TIME,%s,runs=%d,mean_us=%.1f,min_us=%" PRIu32 ",max_us=%" PRIu32
             ",invoke_fails=%" PRIu32 ",cpu_mhz=%" PRIu32,
             m->name, TIMED_RUNS, (double)r->mean_us, min_us, r->max_us, r->invoke_fails,
             (uint32_t)getCpuFrequencyMhz());

    // The example's output, for the eye: y should follow sin(x).
    float max_err = 0.0f;
    for (int i = 0; i < SWEEP_POINTS; i++) {
      const float x = (float)i / (float)SWEEP_POINTS * SWEEP_RANGE;
      const float err = fabsf(sinf(x) - sweep_y[i]);
      if (err > max_err) {
        max_err = err;
      }
      out_line("XY,%s,x=%.4f,y=%.4f,sin=%.4f", m->name, (double)x, (double)sweep_y[i],
               (double)sinf(x));
    }
    out_line("SWEEP,%s,points=%d,max_err=%.4f", m->name, SWEEP_POINTS, (double)max_err);
  }

  check_short_arena(m, model, resolver, r);
}

/**
 * One model's overall result.
 * @param r  Its results.
 * @return true if it ran and every check passed.
 */
static bool model_passed(const model_result_t *r) {
  return r->ran && r->load_ok && r->alloc_ok && r->gold_ok && r->short_ok &&
         r->invoke_fails == 0;
}
#endif  // !LIBCHECK_BASELINE

/**
 * Runs every check once and prints the full report.
 * @return None.
 */
static void run_checks(void) {
  s_runs++;
  out_line("LIBCHECK,ver=%s,run=%" PRIu32 ",core=%s,idf=%s,tflm=%s,esp_nn=%s,cpu_mhz=%" PRIu32
           ",baseline=%d",
           LIBCHECK_VERSION, s_runs, ESP_ARDUINO_VERSION_STR, esp_get_idf_version(),
           TFLM_VERSION, ESP_NN_VERSION, (uint32_t)getCpuFrequencyMhz(), LIBCHECK_BASELINE);
#if LIBCHECK_BASELINE
  out_line("# Baseline build: TFLM is not built. Set LIBCHECK_BASELINE 0 for the checks.");
#else
  out_line("#OPS,op,status");
  out_line("#MODEL,name,bytes,schema,in,out,arena,arena_used,status");
  out_line("#GOLD,name,x,y,sin,err,tol,status");
  out_line("#TIME,name,runs,mean_us,min_us,max_us,invoke_fails,cpu_mhz");
  out_line("#XY,name,x,y,sin");
  out_line("#SWEEP,name,points,max_err");
  out_line("#SHORT,name,arena,short_by,alloc,status");
  s_ops_ok = check_ops();
  for (size_t i = 0; i < MODEL_COUNT; i++) {
    check_model(&kModels[i], &s_results[i]);
  }
  out_line("DONE,run=%" PRIu32 ",ops=%d/%d,int8=%s,float=%s", s_runs, s_ops_ok, ALL_OPS_N,
           model_passed(&s_results[0]) ? "pass" : "FAIL",
           model_passed(&s_results[1]) ? "pass" : "FAIL");
#endif
  out_line("# Send any key to run the checks again. SUM repeats every %d s.",
           SUM_PERIOD_MS / 1000);
}

/**
 * Prints one SUM line: the last run's results plus uptime, free stack and
 * heap. A steady stream of these shows the board did not crash or reboot.
 * @return None.
 */
static void print_sum(void) {
  const uint32_t uptime_s = (uint32_t)(esp_timer_get_time() / 1000000);
  const uint32_t stack_free = (uint32_t)uxTaskGetStackHighWaterMark(NULL);  // Bytes on ESP32
  const uint32_t heap_min = (uint32_t)esp_get_minimum_free_heap_size();
#if LIBCHECK_BASELINE
  out_line("SUM,uptime_s=%" PRIu32 ",runs=%" PRIu32 ",baseline=1,stack_free=%" PRIu32
           ",heap_min=%" PRIu32,
           uptime_s, s_runs, stack_free, heap_min);
#else
  const model_result_t *a = &s_results[0];
  const model_result_t *b = &s_results[1];
  out_line("SUM,uptime_s=%" PRIu32 ",runs=%" PRIu32 ",ops=%d/%d,int8=%s,int8_us=%.1f,"
           "int8_arena=%zu,float=%s,float_us=%.1f,float_arena=%zu,stack_free=%" PRIu32
           ",heap_min=%" PRIu32,
           uptime_s, s_runs, s_ops_ok, ALL_OPS_N, model_passed(a) ? "pass" : "FAIL",
           (double)a->mean_us, a->arena_used, model_passed(b) ? "pass" : "FAIL",
           (double)b->mean_us, b->arena_used, stack_free, heap_min);
#endif
}

/**
 * The only task: owns Serial (core 0), runs the checks at boot and on any
 * key, and prints SUM every SUM_PERIOD_MS.
 * @param param  Unused.
 * @return Never returns.
 */
static void libcheck_task(void *param) {
  (void)param;
  // Before begin(). The boot report is usually gone before a PC opens the
  // port, which is why any key runs the checks again. No Serial.flush()
  // anywhere: with no host reading, the core's flush throws the buffer away.
  Serial.setTxBufferSize(TX_BUF_BYTES);
  Serial.begin(SERIAL_BAUD);
  run_checks();

  int64_t last_sum_us = esp_timer_get_time();
  for (;;) {
    bool rerun = false;
    while (Serial.available() > 0) {
      (void)Serial.read();
      rerun = true;
    }
    if (rerun) {
      run_checks();
      while (Serial.available() > 0) {
        (void)Serial.read();  // Keys sent during the run do not start another
      }
    }
    const int64_t now_us = esp_timer_get_time();
    if (now_us - last_sum_us >= (int64_t)SUM_PERIOD_MS * 1000) {
      last_sum_us = now_us;
      print_sum();
    }
    vTaskDelay(pdMS_TO_TICKS(POLL_MS));
  }
}

/**
 * Starts the check task on core 0, then deletes the Arduino loop task.
 * @return Never returns (its task is deleted).
 */
void setup() {
  const BaseType_t ok = xTaskCreatePinnedToCore(libcheck_task, "libcheck", TASK_STACK_BYTES,
                                                NULL, TASK_PRIO, NULL, TASK_CORE);
  configASSERT(ok == pdPASS);
  (void)ok;  // Keeps the build warning-free if asserts are compiled out
  vTaskDelete(NULL);  // loop() never runs
}

/**
 * Never runs: setup() deletes the loop task. Defined only so the sketch builds.
 * @return None.
 */
void loop() {}
