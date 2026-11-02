/*
 * File:        supervisor.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-02
 * Version:     0.1.0-draft (first draft)
 * Description: supervisor task (core 0, lowest of our priorities). Wakes once
 *              per second with vTaskDelayUntil, drains sys_q without waiting,
 *              and either sends an HLT line or, after any fault, enters the one
 *              safe state and sends an FLT line every second until reset.
 *              Repeating FLT means a monitor opened late still sees it.
 */

// ============================================================================
// Includes
// ============================================================================

#include "esp_timer.h"
#include "config.h"
#include "types.h"
#include "supervisor.h"

// ============================================================================
// Prototypes
// ============================================================================

static void enter_safe_state(const app_ctx_t *ctx);

// ============================================================================
// Implementations
// ============================================================================

void sys_heartbeat(QueueHandle_t sys_q, task_id_t task, uint32_t samples,
                   uint32_t q_drops, uint32_t drdy_timeouts) {
  sys_msg_t m = {};
  m.kind = SYS_HEARTBEAT;
  m.task = task;
  m.samples = samples;
  m.q_drops = q_drops;
  m.drdy_timeouts = drdy_timeouts;
  xQueueSend(sys_q, &m, 0);
}

void sys_fault_and_stop(QueueHandle_t sys_q, task_id_t task, fault_code_t code) {
  sys_msg_t m = {};
  m.kind = SYS_FAULT;
  m.task = task;
  m.fault = code;
  // Waits for space, unlike every other send: the caller stops here anyway,
  // and a lost fault report would turn F1/F2 into a later, vaguer F3.
  xQueueSend(sys_q, &m, portMAX_DELAY);
  for (;;) {
    vTaskSuspend(NULL);
  }
}

/**
 * The one safe state. Same steps, same order, for every fault code: stop
 * sampling and detection. serial_out keeps running so FLT lines still print.
 * Called only by supervisor_task, only once.
 * @param ctx  Handles of the tasks to stop.
 * @return None.
 */
static void enter_safe_state(const app_ctx_t *ctx) {
  vTaskSuspend(ctx->imu_acq_task);  // Harmless if it already stopped itself
  vTaskSuspend(ctx->dsp_task);
}

void supervisor_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;

  // Latest counters, copied from heartbeats.
  uint32_t samples = 0;
  uint32_t sample_q_drops = 0;
  uint32_t drdy_timeouts = 0;
  uint32_t dsp_out_q_drops = 0;
  uint32_t own_out_q_drops = 0;  // HLT / FLT lines this task could not queue

  // Every task gets a full HEARTBEAT_TIMEOUT_MS from now before it counts as
  // missing, which covers IMU init at boot.
  int64_t last_heard_us[TASK_COUNT];
  const int64_t start_us = esp_timer_get_time();
  for (int i = 0; i < TASK_COUNT; i++) {
    last_heard_us[i] = start_us;
  }

  fault_code_t fault = FAULT_NONE;  // First fault reported wins
  bool safe = false;
  TickType_t last_wake = xTaskGetTickCount();

  for (;;) {
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(SUPERVISOR_PERIOD_MS));
    const int64_t now_us = esp_timer_get_time();

    // Drain sys_q without waiting. Keeps draining in the safe state so the
    // remaining senders never see a full queue.
    sys_msg_t m;
    while (xQueueReceive(ctx->sys_q, &m, 0) == pdTRUE) {
      if (m.task >= TASK_COUNT) {
        continue;  // Malformed: ignore rather than index out of range
      }
      if (m.kind == SYS_FAULT) {
        if (fault == FAULT_NONE) {
          fault = m.fault;
        }
        continue;
      }
      last_heard_us[m.task] = now_us;
      if (m.task == TASK_IMU_ACQ) {
        samples = m.samples;
        sample_q_drops = m.q_drops;
        drdy_timeouts = m.drdy_timeouts;
      } else if (m.task == TASK_DSP) {
        dsp_out_q_drops = m.q_drops;
      }
    }

    if (!safe) {
      if (fault == FAULT_NONE) {
        for (int i = 0; i < TASK_COUNT; i++) {
          if (now_us - last_heard_us[i] >= (int64_t)HEARTBEAT_TIMEOUT_MS * 1000) {
            fault = FAULT_HEARTBEAT;
            break;
          }
        }
      }
      if (fault != FAULT_NONE) {
        enter_safe_state(ctx);
        safe = true;
      }
    }

    out_msg_t out = {};
    if (!safe) {
      out.tag = OUT_HLT;
      out.hlt.t_ms = (uint32_t)(now_us / 1000);
      out.hlt.samples = samples;
      out.hlt.sample_q_drops = sample_q_drops;
      out.hlt.out_q_drops = dsp_out_q_drops + own_out_q_drops;
      out.hlt.drdy_timeouts = drdy_timeouts;
    } else {
      out.tag = OUT_FLT;
      out.flt.code = fault;
    }
    if (xQueueSend(ctx->out_q, &out, 0) != pdTRUE) {
      own_out_q_drops++;
    }
  }
}
