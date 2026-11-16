/*
 * File:        supervisor.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      sys_heartbeat takes a sys_msg_t with named counters.
 * Description: supervisor task, plus the two calls every other task uses to
 *              reach it through sys_q: a heartbeat and a fault report.
 */

#ifndef SUPERVISOR_H
#define SUPERVISOR_H

// ============================================================================
// Includes
// ============================================================================

#include "types.h"

// ============================================================================
// Prototypes
// ============================================================================

/**
 * supervisor task entry (core 0, lowest of our priorities). Once per second:
 * drains sys_q, checks heartbeats, and sends HLT to out_q. Sole caller of the
 * safe state; after that it sends FLT to out_q and an IMU-down notice to
 * ble_q once per second until reset.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void supervisor_task(void *param);

/**
 * Sends one heartbeat to sys_q. Never waits: a lost heartbeat is caught by the
 * supervisor's timeout check.
 * @param sys_q  The sys_q handle.
 * @param hb     The caller's task id and the counters it owns (others 0).
 *               kind is set here.
 * @return None.
 */
void sys_heartbeat(QueueHandle_t sys_q, sys_msg_t *hb);

/**
 * Reports a fault to the supervisor and stops the calling task for good. The
 * supervisor alone decides what happens next (the safe state).
 * @param sys_q  The sys_q handle.
 * @param task   Sender.
 * @param code   Fault code.
 * @return Never returns.
 */
[[noreturn]] void sys_fault_and_stop(QueueHandle_t sys_q, task_id_t task,
                                     fault_code_t code);

#endif  // SUPERVISOR_H
