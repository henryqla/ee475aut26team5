/*
 * File:        serial_out.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-23
 * Version:     0.1.4-bench
 * Change:      0.1.4-bench: comments only (no snapshots; L lines in every
 *              build).
 *              0.1.2-bench: comments only (record-mode snapshots).
 *              0.1.0-draft: first draft.
 * Description: serial_out task: sole owner of Serial.
 */

#ifndef SERIAL_OUT_H
#define SERIAL_OUT_H

// ============================================================================
// Prototypes
// ============================================================================

/**
 * serial_out task entry (core 0, above supervisor). Starts Serial, prints the
 * BOOT line and column headers, then prints one line per out_q item (an L
 * line for every sample, plus SEG, HLT, FLT and BLE lines), and repeats the
 * BOOT settings as a CFG line every CFG_PERIOD_MS.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void serial_out_task(void *param);

#endif  // SERIAL_OUT_H
