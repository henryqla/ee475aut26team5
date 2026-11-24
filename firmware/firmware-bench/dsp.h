/*
 * File:        dsp.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-18
 * Version:     0.1.2-bench
 * Change:      Comments only: re-arm rule and record-mode snapshots.
 *              0.2.0-draft: Adds 1 s segments and phone time; sends to ble_q too.
 * Description: dsp task: axis mapping, gravity removal, 1 s segment summaries,
 *              thresholds, lockout, re-arm, and pothole / speed bump
 *              classification. Record builds also capture one snapshot per
 *              trigger.
 */

#ifndef DSP_H
#define DSP_H

// ============================================================================
// Prototypes
// ============================================================================

/**
 * dsp task entry (core 1, below imu_acq). Processes every sample from
 * sample_q and sends S, SEG and EVT lines to out_q (SEG and EVT to ble_q
 * too). Record builds also send each full snapshot to out_q by pointer.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void dsp_task(void *param);

#endif  // DSP_H
