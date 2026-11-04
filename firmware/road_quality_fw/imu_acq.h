/*
 * File:        imu_acq.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-02
 * Version:     0.1.0-draft (first draft)
 * Description: imu_acq task: sole owner of the SPI bus and the IMU.
 */

#ifndef IMU_ACQ_H
#define IMU_ACQ_H

// ============================================================================
// Prototypes
// ============================================================================

/**
 * imu_acq task entry (core 1, highest of our priorities). Initializes the IMU,
 * then reads one sample per INT1 data-ready and sends it to sample_q.
 * @param param  Pointer to the app_ctx_t from setup(). Must outlive the task.
 * @return Never returns.
 */
void imu_acq_task(void *param);

#endif  // IMU_ACQ_H
