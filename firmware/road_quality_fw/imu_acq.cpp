/*
 * File:        imu_acq.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.2.0-draft
 * Change:      Heartbeat sent as a sys_msg_t with named counters. PCB target
 *              (same pins). No change to acquisition.
 * Description: imu_acq task (core 1, highest of our priorities). Sole owner of
 *              the SPI bus and the LSM6DSO32X. Initializes the IMU, then for
 *              each INT1 data-ready notification reads one sample, stamps it
 *              with the sample count and esp_timer microseconds, and sends it
 *              to sample_q without waiting. Once per second it re-reads
 *              WHO_AM_I and sends a heartbeat. Detects F1 and F2.
 */

// ============================================================================
// Includes
// ============================================================================

#include <SPI.h>
#include <Adafruit_LSM6DSO32.h>
#include "esp_timer.h"
#include "board_pins.h"
#include "config.h"
#include "types.h"
#include "imu_acq.h"
#include "supervisor.h"

// ============================================================================
// Macros
// ============================================================================

// WHO_AM_I (register 0Fh) value from the LSM6DSO32X datasheet. The library's
// LSM6DSO32_CHIP_ID is the same 0x6C, so begin_SPI() accepts the X variant.
#define IMU_WHO_AM_I  0x6C

// config.h values -> library enums. An unsupported value fails the build
// instead of running at some other setting.
#if ACCEL_FS_G == 4
#define IMU_ACCEL_RANGE  LSM6DSO32_ACCEL_RANGE_4_G
#elif ACCEL_FS_G == 8
#define IMU_ACCEL_RANGE  LSM6DSO32_ACCEL_RANGE_8_G
#elif ACCEL_FS_G == 16
#define IMU_ACCEL_RANGE  LSM6DSO32_ACCEL_RANGE_16_G
#elif ACCEL_FS_G == 32
#define IMU_ACCEL_RANGE  LSM6DSO32_ACCEL_RANGE_32_G
#else
#error "ACCEL_FS_G must be 4, 8, 16 or 32"
#endif

#if GYRO_FS_DPS == 125
#define IMU_GYRO_RANGE  LSM6DS_GYRO_RANGE_125_DPS
#elif GYRO_FS_DPS == 250
#define IMU_GYRO_RANGE  LSM6DS_GYRO_RANGE_250_DPS
#elif GYRO_FS_DPS == 500
#define IMU_GYRO_RANGE  LSM6DS_GYRO_RANGE_500_DPS
#elif GYRO_FS_DPS == 1000
#define IMU_GYRO_RANGE  LSM6DS_GYRO_RANGE_1000_DPS
#elif GYRO_FS_DPS == 2000
#define IMU_GYRO_RANGE  LSM6DS_GYRO_RANGE_2000_DPS
#else
#error "GYRO_FS_DPS must be 125, 250, 500, 1000 or 2000"
#endif

#if IMU_ODR_HZ == 416
#define IMU_DATA_RATE  LSM6DS_RATE_416_HZ
#else
#error "IMU_ODR_HZ: only 416 is mapped"
#endif

// ============================================================================
// Globals
// ============================================================================

// The library's chipID() is protected. This subclass only makes it callable,
// so every SPI access still goes through the library and this task.
class ImuDevice : public Adafruit_LSM6DSO32 {
 public:
  uint8_t who_am_i(void) { return chipID(); }
};

static ImuDevice s_imu;

// This task's handle, for the ISR. Written once, before the ISR is attached.
static TaskHandle_t s_imu_task = NULL;

// ============================================================================
// Prototypes
// ============================================================================

static void imu_int1_isr(void);  // IRAM_ATTR goes on the definition only
static bool imu_init(void);
static void imu_read(imu_sample_t *s);

// ============================================================================
// Implementations
// ============================================================================

/**
 * INT1 rising-edge ISR. Only wakes imu_acq: no SPI, no Serial.
 * @return None.
 */
static void IRAM_ATTR imu_int1_isr(void) {
  BaseType_t woken = pdFALSE;
  vTaskNotifyGiveFromISR(s_imu_task, &woken);
  // If imu_acq now outranks the interrupted task, switch to it on ISR exit.
  portYIELD_FROM_ISR(woken);
}

/**
 * Starts the SPI bus and configures the IMU: full scales, ODR, and accel
 * data-ready on INT1. Reads the settings back to check the bus both ways.
 * @return true if the IMU answered and took every setting, false otherwise.
 */
static bool imu_init(void) {
  // Pins passed explicitly. The S3's default SPI pins collide with this
  // wiring, and its default MOSI is GPIO11, our INT1 line. The library's own
  // SPI.begin() call then returns early because the bus is already started.
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_IMU_CS);

  // Hardware SPI at SPI_CLOCK_HZ; the library drives CS as a plain GPIO.
  // Fails if WHO_AM_I is not 0x6C. On success it has soft-reset the chip and
  // set block data update (BDU).
  if (!s_imu.begin_SPI(PIN_IMU_CS, &SPI, 0, SPI_CLOCK_HZ)) {
    return false;
  }

  s_imu.setAccelRange(IMU_ACCEL_RANGE);
  s_imu.setGyroRange(IMU_GYRO_RANGE);
  s_imu.setAccelDataRate(IMU_DATA_RATE);
  s_imu.setGyroDataRate(IMU_DATA_RATE);

  // A bad MOSI connection shows up here, instead of later as wrongly scaled data.
  if (s_imu.getAccelRange() != IMU_ACCEL_RANGE ||
      s_imu.getGyroRange() != IMU_GYRO_RANGE ||
      s_imu.getAccelDataRate() != IMU_DATA_RATE ||
      s_imu.getGyroDataRate() != IMU_DATA_RATE) {
    return false;
  }

  // INT1 = accel data-ready only. The gyro runs at the same ODR, so one source
  // gives one edge per sample. The pin keeps its reset defaults: active high,
  // push-pull, latched until the data is read.
  s_imu.configInt1(false, false, true);
  return true;
}

/**
 * Reads one sample and converts it to g and dps. getEvent() does one 14-byte
 * burst (temp, gyro, accel) plus two 1-byte reads of the range registers.
 * @param s  Filled with ax..gz. n and t_us are left for the caller.
 * @return None.
 */
static void imu_read(imu_sample_t *s) {
  sensors_event_t accel, gyro, temp;
  s_imu.getEvent(&accel, &gyro, &temp);

  // The library reports m/s^2 and rad/s, computed from g and dps with these
  // same constants, so this undoes its conversion.
  s->ax = accel.acceleration.x / SENSORS_GRAVITY_STANDARD;
  s->ay = accel.acceleration.y / SENSORS_GRAVITY_STANDARD;
  s->az = accel.acceleration.z / SENSORS_GRAVITY_STANDARD;
  s->gx = gyro.gyro.x * SENSORS_RADS_TO_DPS;
  s->gy = gyro.gyro.y * SENSORS_RADS_TO_DPS;
  s->gz = gyro.gyro.z * SENSORS_RADS_TO_DPS;
}

void imu_acq_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;
  s_imu_task = xTaskGetCurrentTaskHandle();

  if (!imu_init()) {
    sys_fault_and_stop(ctx->sys_q, TASK_IMU_ACQ, FAULT_IMU_INIT);  // F1
  }

  // Pull-down so a broken INT1 connection reads low (no edges, then F2)
  // instead of floating and firing at random. The IMU drives the line
  // push-pull, so the weak pull-down does not fight it.
  pinMode(PIN_IMU_INT1, INPUT_PULLDOWN);
  // Attached from this task, so the GPIO interrupt is allocated on core 1.
  attachInterrupt(digitalPinToInterrupt(PIN_IMU_INT1), imu_int1_isr, RISING);

  imu_sample_t s = {};
  // Data-ready has been latched high since the ODR was set, before the ISR
  // existed, so no rising edge would ever come. Reading clears it.
  imu_read(&s);

  uint32_t n = 0;               // Data-ready samples so far
  uint32_t sample_q_drops = 0;
  uint32_t drdy_timeouts = 0;
  int64_t last_good_us = esp_timer_get_time();  // Last data-ready sample
  int64_t last_check_us = last_good_us;         // Last WHO_AM_I + heartbeat

  for (;;) {
    // pdTRUE clears the count: several edges before this task runs still mean
    // one read, of the newest data.
    const uint32_t woke = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(DRDY_TIMEOUT_MS));
    const int64_t now_us = esp_timer_get_time();

    imu_read(&s);

    if (woke > 0) {
      s.n = n++;
      s.t_us = now_us;
      last_good_us = now_us;
      if (xQueueSend(ctx->sample_q, &s, 0) != pdTRUE) {
        sample_q_drops++;  // dsp is behind. Drop and count, never wait
      }
    } else {
      // No edge for DRDY_TIMEOUT_MS. The read above clears a latched
      // data-ready so edges can resume. The sample is not sent: data-ready
      // did not pace it, so its timestamp would be wrong.
      drdy_timeouts++;
    }

    if (now_us - last_good_us >= (int64_t)IMU_STALL_MS * 1000) {
      sys_fault_and_stop(ctx->sys_q, TASK_IMU_ACQ, FAULT_IMU_LOST);  // F2
    }

    if (now_us - last_check_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
      // Advance by exactly one period (not to now_us) so this cadence does
      // not drift against the supervisor's 1 s tick. Otherwise HLT samples
      // would now and then repeat one second and jump ~832 the next.
      last_check_us += (int64_t)HEARTBEAT_PERIOD_MS * 1000;
      // A broken MISO or CS connection still lets samples "arrive" (INT1
      // keeps firing), but they are garbage. WHO_AM_I catches it.
      if (s_imu.who_am_i() != IMU_WHO_AM_I) {
        sys_fault_and_stop(ctx->sys_q, TASK_IMU_ACQ, FAULT_IMU_LOST);  // F2
      }
      sys_msg_t hb = {};
      hb.task = TASK_IMU_ACQ;
      hb.samples = n;
      hb.drdy_timeouts = drdy_timeouts;
      hb.sample_q_drops = sample_q_drops;
      sys_heartbeat(ctx->sys_q, &hb);
    }
  }
}
