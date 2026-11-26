/*
 * File:        dsp.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-23
 * Version:     0.1.4-bench
 * Change:      0.1.4-bench (manual-recording-overhaul-handoff.md, request A):
 *              no detection; every sample goes out as an L line.
 *              dsp_axis_map() gives the mapping text for BOOT.
 *              0.1.2-bench: comments only (re-arm rule, snapshots).
 *              0.2.0-draft: 1 s segments and phone time; sends to ble_q too.
 * Description: dsp task: axis mapping, gravity removal (HPF), gyro bias
 *              removal, 1 s segment summaries, and an L line for every sample.
 */

#ifndef DSP_H
#define DSP_H

// ============================================================================
// Prototypes
// ============================================================================

/**
 * dsp task entry (core 1, below imu_acq). Processes every sample from
 * sample_q: sends one L line per sample and one SEG per second to out_q, and
 * each SEG to ble_q too, all without waiting.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void dsp_task(void *param);

/**
 * The axis mapping map_axes() applies, as BOOT key=value fields, so every
 * capture records it. Defined next to map_axes() in dsp.cpp so the two
 * change together.
 * @return e.g. "vert=az,roll=gy,pitch=gx" (car axis = chip axis).
 */
const char *dsp_axis_map(void);

#endif  // DSP_H
