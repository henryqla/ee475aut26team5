/*
 * File:        dsp.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      Adds 1 s segments and phone time; sends to ble_q too.
 * Description: dsp task: axis mapping, gravity removal, 1 s segment summaries,
 *              thresholds, lockout, and pothole / speed bump classification.
 */

#ifndef DSP_H
#define DSP_H

// ============================================================================
// Prototypes
// ============================================================================

/**
 * dsp task entry (core 1, below imu_acq). Processes every sample from
 * sample_q and sends S and EVT lines to out_q.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void dsp_task(void *param);

#endif  // DSP_H
