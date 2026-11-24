/*
 * File:        inference.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-24
 * Version:     0.1.5-bench
 * Change:      0.1.5-bench (training-live-inference-handoff.md, request F):
 *              new file.
 * Description: Live inference: the CNN in model_data.h on a sliding window of
 *              the model channels (z_hp, roll, pitch). dsp feeds every sample
 *              in with inference_add_sample(); every MODEL_HOP samples the
 *              inference task (core 1, below imu_acq and dsp) runs the model
 *              on the last MODEL_WINDOW samples and sends PROB and DET lines to
 *              out_q. serial_out reads the model facts for BOOT and CFG.
 */

#ifndef INFERENCE_H
#define INFERENCE_H

// ============================================================================
// Includes
// ============================================================================

#include "types.h"

// ============================================================================
// Globals: types
// ============================================================================

// What the model load and boot self-test found. Filled by the inference task
// before inference_start() returns, read-only afterwards.
typedef struct {
  const char *model_version;  // MODEL_VERSION from model_data.h
  const char *classes;        // Class names in output order, '|'-separated
  uint32_t    window;         // MODEL_WINDOW, samples
  uint32_t    hop;            // MODEL_HOP, samples
  uint32_t    arena_used;     // Bytes of the tensor arena the model used (0 if not loaded)
  bool        selftest_ran;   // false if the load failed before the self-test
  bool        selftest_pass;  // Every output within SELFTEST_TOL
  float       selftest_diff;  // Largest |board - stored| probability seen
} inference_boot_t;

// ============================================================================
// Prototypes
// ============================================================================

/**
 * Creates the inference task (static stack, core 1, below imu_acq and dsp) and
 * waits up to INFER_READY_TIMEOUT_MS while it loads the model and runs the
 * boot self-test. setup() calls it before creating any other task, so the
 * TFLite Micro error text (which goes straight to the console) never mixes
 * with Serial lines, and BOOT can carry the self-test result. A model fault
 * (F5, F6, F7, F8) stops inference only; the task stays alive and sends
 * heartbeats carrying the fault.
 * @param ctx  The shared context (queues). Must outlive the task.
 * @return None.
 */
void inference_start(app_ctx_t *ctx);

/**
 * Called by dsp for every sample, with the values it puts in the L line.
 * Never waits. Writes the ring buffer; every MODEL_HOP samples, if the last
 * MODEL_WINDOW samples were all ok with no gap in n (F5), hands the window to
 * the inference task, or counts a skip if it is still busy.
 * @param n      Sample count (L line n).
 * @param epoch  Sample epoch (a new one after an IMU recovery restarts the run).
 * @param z_hp   High-passed vertical acceleration, g.
 * @param roll   Roll rate, dps, bias removed.
 * @param pitch  Pitch rate, dps, bias removed.
 * @param ok     Gyro bias ready (the L line's ok).
 * @return None.
 */
void inference_add_sample(uint32_t n, uint32_t epoch, float z_hp, float roll, float pitch,
                          bool ok);

/**
 * @return The model facts and self-test result (read-only).
 */
const inference_boot_t *inference_boot(void);

/**
 * @return The model fault (F5-F8) that stopped inference, or FAULT_NONE while
 *         it runs. Safe to call from any task.
 */
fault_code_t inference_fault(void);

/**
 * @param k  Class index.
 * @return Its name from model_data.h, or "?" if out of range.
 */
const char *inference_class_name(uint8_t k);

#endif  // INFERENCE_H
