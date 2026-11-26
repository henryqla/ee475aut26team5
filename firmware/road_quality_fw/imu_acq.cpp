/*
 * File:        imu_acq.cpp
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-16
 * Version:     0.1.1-bench
 * Change:      IMU recovery. F1 and F2 no longer stop this task for good: it
 *              reports SYS_IMU_DOWN, retries every IMU_RETRY_MS (heartbeats
 *              keep going), and once WHO_AM_I reads right IMU_RECOVER_GOOD_READS
 *              times in a row, re-runs the full init (a power-cycled chip comes
 *              back with its sensors off and INT1 unrouted, and WHO_AM_I alone
 *              cannot tell). Then it reports SYS_IMU_UP and bumps the sample
 *              epoch so dsp restarts its filters.
 *              0.1.0-bench: comments only, for the LSM6DSO32 breakout.
 * Description: imu_acq task (core 1, highest of our priorities). Sole owner of
 *              the SPI bus and the IMU (bench: LSM6DSO32 breakout; PCB:
 *              LSM6DSO32X). Initializes the IMU, then for
 *              each INT1 data-ready notification reads one sample, stamps it
 *              with the sample count and esp_timer microseconds, and sends it
 *              to sample_q without waiting. Once per second it re-reads
 *              WHO_AM_I and sends a heartbeat. Detects F1 and F2 and recovers
 *              from both.
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

// WHO_AM_I (register 0Fh). 0x6C for both the plain LSM6DSO32 (bench breakout)
// and the LSM6DSO32X (PCB). The library's LSM6DSO32_CHIP_ID is the same 0x6C
// (Adafruit LSM6DS 4.7.4, Adafruit_LSM6DSO32.h line 23), so begin_SPI()
// accepts either chip.
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

// Counters this task reports in its heartbeat. Owned by imu_acq_task.
typedef struct {
  uint32_t n;               // Data-ready samples so far
  uint32_t sample_q_drops;
  uint32_t drdy_timeouts;
  uint32_t recoveries;      // Times the IMU came back after F1/F2
} imu_stats_t;

// ============================================================================
// Prototypes
// ============================================================================

static void imu_int1_isr(void);  // IRAM_ATTR goes on the definition only
static bool imu_init(void);
static void imu_read(imu_sample_t *s);
static void imu_start(imu_sample_t *s);
static void send_heartbeat(const app_ctx_t *ctx, const imu_stats_t *st);
static void imu_recover(const app_ctx_t *ctx, fault_code_t code, imu_stats_t *st,
                        imu_sample_t *s);

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

/**
 * Clears what piled up before sampling (re)starts. Data-ready has been latched
 * high since the ODR was set, so no rising edge would ever come; reading
 * clears it. Notifications are cleared first, so an edge that arrives after
 * the read is kept.
 * @param s  Scratch sample, overwritten.
 * @return None.
 */
static void imu_start(imu_sample_t *s) {
  ulTaskNotifyTake(pdTRUE, 0);  // Drop stale INT1 notifications
  imu_read(s);
}

/**
 * Sends this task's heartbeat with its counters.
 * @param ctx  For sys_q.
 * @param st   Counters.
 * @return None.
 */
static void send_heartbeat(const app_ctx_t *ctx, const imu_stats_t *st) {
  sys_msg_t hb = {};
  hb.task = TASK_IMU_ACQ;
  hb.samples = st->n;
  hb.drdy_timeouts = st->drdy_timeouts;
  hb.sample_q_drops = st->sample_q_drops;
  hb.imu_recoveries = st->recoveries;
  sys_heartbeat(ctx->sys_q, &hb);
}

/**
 * Reports the IMU down, then retries until it is back and configured. Keeps
 * sending heartbeats, so the supervisor sees a lost IMU (F1/F2), not a stuck
 * task (F3). The INT1 pull-down keeps the ISR quiet while the IMU is off.
 * WHO_AM_I is checked first because it is one cheap register read; the full
 * init (which allocates in the library) runs only after it reads right
 * IMU_RECOVER_GOOD_READS times in a row. s_imu has always been through
 * begin_SPI() once by now (it creates its SPI device before checking
 * WHO_AM_I), so who_am_i() is safe even after F1.
 * @param ctx   For sys_q.
 * @param code  FAULT_IMU_INIT (boot) or FAULT_IMU_LOST (runtime).
 * @param st    Counters: recoveries is incremented on return.
 * @param s     Scratch sample for imu_start().
 * @return Once the IMU is configured and sampling can resume.
 */
static void imu_recover(const app_ctx_t *ctx, fault_code_t code, imu_stats_t *st,
                        imu_sample_t *s) {
  sys_msg_t down = {};
  down.kind = SYS_IMU_DOWN;
  down.task = TASK_IMU_ACQ;
  down.fault = code;
  // Waits for space, like sys_fault_and_stop: losing this report would hide
  // the fault. The supervisor drains sys_q every second.
  xQueueSend(ctx->sys_q, &down, portMAX_DELAY);

  uint32_t good = 0;
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(IMU_RETRY_MS));
    send_heartbeat(ctx, st);

    good = (s_imu.who_am_i() == IMU_WHO_AM_I) ? good + 1 : 0;
    if (good < IMU_RECOVER_GOOD_READS) {
      continue;
    }
    if (imu_init()) {  // Soft reset, ranges, ODR, INT1 route, read-back check
      break;
    }
    good = 0;  // Answered but would not take the settings: start over
  }

  imu_start(s);
  st->recoveries++;

  sys_msg_t up = {};
  up.kind = SYS_IMU_UP;
  up.task = TASK_IMU_ACQ;
  xQueueSend(ctx->sys_q, &up, portMAX_DELAY);
}

void imu_acq_task(void *param) {
  const app_ctx_t *ctx = (const app_ctx_t *)param;
  s_imu_task = xTaskGetCurrentTaskHandle();

  const bool init_ok = imu_init();

  // Pull-down so a broken INT1 connection reads low (no edges, then F2)
  // instead of floating and firing at random. The IMU drives the line
  // push-pull, so the weak pull-down does not fight it. Attached even if init
  // failed, so a recovered IMU finds it ready.
  pinMode(PIN_IMU_INT1, INPUT_PULLDOWN);
  // Attached from this task, so the GPIO interrupt is allocated on core 1.
  attachInterrupt(digitalPinToInterrupt(PIN_IMU_INT1), imu_int1_isr, RISING);

  imu_stats_t st = {};
  uint32_t epoch = 0;  // Goes into every sample; +1 per recovery
  imu_sample_t s = {};
  if (init_ok) {
    imu_start(&s);
  } else {
    imu_recover(ctx, FAULT_IMU_INIT, &st, &s);  // F1: wait for the IMU
    epoch++;
  }

  int64_t last_good_us = esp_timer_get_time();  // Last data-ready sample
  int64_t last_check_us = last_good_us;         // Last WHO_AM_I + heartbeat

  for (;;) {
    // pdTRUE clears the count: several edges before this task runs still mean
    // one read, of the newest data.
    const uint32_t woke = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(DRDY_TIMEOUT_MS));
    const int64_t now_us = esp_timer_get_time();

    imu_read(&s);

    if (woke > 0) {
      s.n = st.n++;
      s.epoch = epoch;
      s.t_us = now_us;
      last_good_us = now_us;
      if (xQueueSend(ctx->sample_q, &s, 0) != pdTRUE) {
        st.sample_q_drops++;  // dsp is behind. Drop and count, never wait
      }
    } else {
      // No edge for DRDY_TIMEOUT_MS. The read above clears a latched
      // data-ready so edges can resume. The sample is not sent: data-ready
      // did not pace it, so its timestamp would be wrong.
      st.drdy_timeouts++;
    }

    bool lost = (now_us - last_good_us >= (int64_t)IMU_STALL_MS * 1000);

    if (!lost && now_us - last_check_us >= (int64_t)HEARTBEAT_PERIOD_MS * 1000) {
      // Advance by exactly one period (not to now_us) so this cadence does
      // not drift against the supervisor's 1 s tick. Otherwise HLT samples
      // would now and then repeat one second and jump ~832 the next.
      last_check_us += (int64_t)HEARTBEAT_PERIOD_MS * 1000;
      // A broken MISO or CS connection still lets samples "arrive" (INT1
      // keeps firing), but they are garbage. WHO_AM_I catches it.
      if (s_imu.who_am_i() != IMU_WHO_AM_I) {
        lost = true;
      } else {
        send_heartbeat(ctx, &st);
      }
    }

    if (lost) {
      // F2: power loss, or SCK/MOSI/MISO/CS/INT1 came loose. Retry until the
      // IMU is back, then restart the timers and tell dsp via the epoch.
      imu_recover(ctx, FAULT_IMU_LOST, &st, &s);
      epoch++;
      last_good_us = esp_timer_get_time();
      last_check_us = last_good_us;
    }
  }
}
