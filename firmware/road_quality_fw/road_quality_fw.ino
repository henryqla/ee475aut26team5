/*
 * File:        road_quality_fw.ino
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-02
 * Version:     0.1.0-draft (first draft)
 * Description: Phase 1 road quality firmware for a breadboarded
 *              ESP32-S3-DevKitC-1 N8R8 with an LSM6DSO32X IMU on SPI. Reads the
 *              IMU at 416 Hz, prints samples, events, and health to Serial, and
 *              classifies potholes and speed bumps by thresholding.
 *
 *              setup() only creates the queues and the four tasks, then deletes
 *              its own task, so loop() never runs. Each task initializes the
 *              peripheral it owns. Core 1: imu_acq, dsp. Core 0: serial_out,
 *              supervisor.
 *
 *              Build: Arduino IDE 2.3.10, esp32 core 3.3.10, board "ESP32S3 Dev
 *              Module", Flash Size 8MB, PSRAM Disabled, USB CDC On Boot
 *              Disabled. Libraries: Adafruit LSM6DS, Adafruit BusIO, Adafruit
 *              Unified Sensor.
 */

// ============================================================================
// Includes
// ============================================================================

#include "config.h"
#include "types.h"
#include "imu_acq.h"
#include "dsp.h"
#include "serial_out.h"
#include "supervisor.h"

// ============================================================================
// Globals
// ============================================================================

// Queue and task handles. Filled here before any task that reads them exists,
// read-only afterwards.
static app_ctx_t s_ctx;

// ============================================================================
// Prototypes
// ============================================================================

static void start_task(TaskFunction_t fn, const char *name, UBaseType_t prio,
                       BaseType_t core, TaskHandle_t *handle);

// ============================================================================
// Implementations
// ============================================================================

/**
 * Creates one task pinned to a core, with the shared context as its parameter.
 * @param fn      Task entry function.
 * @param name    Task name (shows in debug tools).
 * @param prio    FreeRTOS priority.
 * @param core    Core to pin to (0 or 1).
 * @param handle  Receives the task handle, or NULL if not needed.
 * @return None. Halts via configASSERT if the task could not be created.
 */
static void start_task(TaskFunction_t fn, const char *name, UBaseType_t prio,
                       BaseType_t core, TaskHandle_t *handle) {
  const BaseType_t ok = xTaskCreatePinnedToCore(fn, name, TASK_STACK_BYTES,
                                                &s_ctx, prio, handle, core);
  configASSERT(ok == pdPASS);
  (void)ok;  // Keeps the build warning-free if asserts are compiled out
}

/**
 * Creates the queues and tasks, then deletes the Arduino loop task.
 * @return Never returns (its task is deleted).
 */
void setup() {
  s_ctx.sample_q = xQueueCreate(SAMPLE_Q_LEN, sizeof(imu_sample_t));
  s_ctx.out_q = xQueueCreate(OUT_Q_LEN, sizeof(out_msg_t));
  s_ctx.sys_q = xQueueCreate(SYS_Q_LEN, sizeof(sys_msg_t));
  // These fail only if the heap is exhausted at boot: a build problem, not a
  // runtime fault, and no task exists yet to report it. configASSERT prints
  // the file and line on UART0 and reboots.
  configASSERT(s_ctx.sample_q != NULL);
  configASSERT(s_ctx.out_q != NULL);
  configASSERT(s_ctx.sys_q != NULL);

  // serial_out first so BOOT prints as early as possible. supervisor last,
  // because it needs the imu_acq and dsp handles.
  start_task(serial_out_task, "serial_out", PRIO_SERIAL_OUT, CORE_IO, NULL);
  start_task(dsp_task, "dsp", PRIO_DSP, CORE_SAMPLING, &s_ctx.dsp_task);
  start_task(imu_acq_task, "imu_acq", PRIO_IMU_ACQ, CORE_SAMPLING, &s_ctx.imu_acq_task);
  start_task(supervisor_task, "supervisor", PRIO_SUPERVISOR, CORE_IO, NULL);

  vTaskDelete(NULL);  // loop() never runs
}

/**
 * Never runs: setup() deletes the loop task. Defined only so the sketch builds.
 * @return None.
 */
void loop() {}
