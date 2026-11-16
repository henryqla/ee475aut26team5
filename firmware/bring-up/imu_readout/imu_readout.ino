/*
 * File:        imu_readout.ino
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-03
 * Version:     0.1.0-draft (first draft)
 * Description: PCB bring-up test tool, not product firmware. Reads the
 *              LSM6DSO32X IMU over SPI on the team's custom PCB
 *              (ESP32-S3-WROOM-1-N16R8) and prints one sample per second to
 *              Serial: accel in g, gyro in dps, temperature in C, on one
 *              comma-separated line. If the IMU does not answer, does not
 *              take its settings, or stops answering later (WHO_AM_I is
 *              checked before every read), it prints an ERR line once per
 *              second instead and never touches the IMU again.
 *
 *              Output lines: "#" = banner, then one CSV header line, then
 *              either CSV data lines or ERR lines.
 *
 *              Plain setup()/loop(), no tasks (decision B1). IMU settings are
 *              for bring-up only (decision B3); the product settings stay
 *              undecided.
 *
 *              Build: Arduino IDE 2.3.10, esp32 core 3.3.10, board "ESP32S3 Dev
 *              Module", Flash Size 16MB, PSRAM Disabled, USB CDC On Boot
 *              Enabled, USB Mode "Hardware CDC and JTAG", Upload Mode "UART0 /
 *              Hardware CDC". Libraries: Adafruit LSM6DS, Adafruit BusIO,
 *              Adafruit Unified Sensor.
 *
 *              Serial and upload use native USB (GPIO19/20). If the port does
 *              not appear, hold BOOT and press reset to enter download mode.
 *
 *              Pins from pin-map.md v0.1, as copied in pcb-bringup-context.md
 *              v0.5. Any pin change is a wiring change: Henry approves it.
 *              Never touched here: GPIO11 and GPIO47 (IMU Int_1 and Int_2, the
 *              IMU drives them), GPIO35-37 (PSRAM), GPIO0, 3, 45, 46
 *              (strapping), GPIO19 and 20 (native USB).
 */

// ============================================================================
// Includes
// ============================================================================

#include <SPI.h>
#include <Adafruit_LSM6DSO32.h>

// ============================================================================
// Macros
// ============================================================================

#define SKETCH_VERSION    "0.1.0-draft"  // Printed in the banner. Bump on every change

// Pins (pin-map.md v0.1). The only pins this sketch uses.
#define PIN_SPI_SCK       14  // SPI SCK
#define PIN_SPI_MISO      12  // SPI MISO (IMU SDO)
#define PIN_SPI_MOSI      13  // SPI MOSI (IMU SDI)
#define PIN_IMU_CS        21  // IMU CS, active low. The library drives it as a GPIO

// IMU settings, bring-up only (decision B3). If you change one, also change
// the banner text in setup().
#define SPI_CLOCK_HZ      1000000                    // Slow clock tolerates poor signal integrity, so a failure points at wiring, not timing. Part allows 10 MHz
#define IMU_ACCEL_RATE    LSM6DS_RATE_416_HZ         // Project rate, so bring-up exercises the real sensor setting
#define IMU_GYRO_RATE     LSM6DS_RATE_416_HZ         // Same reason as the accel rate
#define IMU_ACCEL_RANGE   LSM6DSO32_ACCEL_RANGE_8_G  // +-8 g: room for taps without clipping, ~0.24 mg per count
#define IMU_GYRO_RANGE    LSM6DS_GYRO_RANGE_500_DPS  // +-500 dps: flipping the board by hand can exceed 250 dps

// WHO_AM_I (register 0Fh) of the LSM6DSO32X, from its datasheet. The library
// checks for the same 0x6C (LSM6DSO32_CHIP_ID).
#define IMU_WHO_AM_I      0x6C

// Serial and timing
#define SERIAL_BAUD       115200  // Ignored by native USB CDC; kept so the call reads normally
#define SERIAL_WAIT_MS    2000    // Longest boot wait for a host, so an open monitor sees the banner
#define PRINT_PERIOD_MS   1000    // One data (or ERR) line per second

// ============================================================================
// Globals
// ============================================================================

// The library's chipID() is protected. This subclass only makes it callable,
// so the ERR line can show the WHO_AM_I byte actually read. Same approach as
// firmware/road_quality_fw/imu_acq.cpp.
class ImuDevice : public Adafruit_LSM6DSO32 {
 public:
  uint8_t who_am_i(void) { return chipID(); }
};

static ImuDevice s_imu;

// Empty while the IMU is good. Otherwise the reason sampling stopped, written
// once (by imu_init() or loop()) and printed by loop() every PRINT_PERIOD_MS.
static char s_error[96] = "";

static uint32_t s_last_print_ms = 0;  // millis() of the last printed line

// ============================================================================
// Prototypes
// ============================================================================

static void set_who_am_i_error(const char *what, uint8_t id);
static bool imu_init(void);
static void print_sample(void);

// ============================================================================
// Implementations
// ============================================================================

/**
 * Writes a WHO_AM_I failure to s_error, with a hint at the likely cause.
 * @param what  Short label for when it failed: "IMU not found" or "IMU lost".
 * @param id    The WHO_AM_I byte actually read.
 * @return None.
 */
static void set_who_am_i_error(const char *what, uint8_t id) {
  // MISO floats when nothing drives it, so 0x00 or 0xFF usually means the
  // IMU is not answering at all. Any other value means the bits arrive but
  // are corrupt, or the part is not an LSM6DSO32X.
  const bool no_answer = (id == 0x00 || id == 0xFF);
  snprintf(s_error, sizeof(s_error), "%s: WHO_AM_I=0x%02X, expected 0x%02X (%s)",
           what, id, IMU_WHO_AM_I,
           no_answer ? "no answer: check power, CS, MISO" : "corrupt or wrong part");
}

/**
 * Starts the SPI bus and configures the IMU with the bring-up settings, then
 * reads the settings back. On failure, writes the reason to s_error.
 * @return true if the IMU answered and took every setting, false otherwise.
 */
static bool imu_init(void) {
  // Pins passed explicitly. The core's default S3 SPI pins (SCK 12, MISO 13,
  // MOSI 11) collide with this board, and MOSI 11 would drive Int_1 against
  // the IMU's own output. begin_SPI() calls SPI.begin() again with no pins;
  // that call returns early because the bus is already started, so these
  // pins stay. CS is only stored here, not attached as a hardware CS.
  if (!SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_IMU_CS)) {
    snprintf(s_error, sizeof(s_error), "SPI.begin() failed");
    return false;
  }

  // Fails if WHO_AM_I is not 0x6C. On success the library has soft-reset the
  // chip, set block data update, and left it at 104 Hz, so the B3 settings
  // below must come after this call.
  if (!s_imu.begin_SPI(PIN_IMU_CS, &SPI, 0, SPI_CLOCK_HZ)) {
    set_who_am_i_error("IMU not found", s_imu.who_am_i());
    return false;
  }

  s_imu.setAccelRange(IMU_ACCEL_RANGE);
  s_imu.setGyroRange(IMU_GYRO_RANGE);
  s_imu.setAccelDataRate(IMU_ACCEL_RATE);
  s_imu.setGyroDataRate(IMU_GYRO_RATE);

  // WHO_AM_I only proves that a read works. Reading the settings back proves
  // the writes landed too, which a bad MOSI line would break.
  if (s_imu.getAccelRange() != IMU_ACCEL_RANGE ||
      s_imu.getGyroRange() != IMU_GYRO_RANGE ||
      s_imu.getAccelDataRate() != IMU_ACCEL_RATE ||
      s_imu.getGyroDataRate() != IMU_GYRO_RATE) {
    snprintf(s_error, sizeof(s_error), "IMU settings did not read back: writes not landing (check MOSI)");
    return false;
  }

  return true;
}

/**
 * Reads one sample and prints it as one CSV line: ax,ay,az in g, gx,gy,gz in
 * dps, temperature in C.
 * @return None.
 */
static void print_sample(void) {
  // One 14-byte burst (temp, gyro, accel) plus two 1-byte range reads.
  sensors_event_t accel, gyro, temp;
  s_imu.getEvent(&accel, &gyro, &temp);

  // The library reports m/s^2 and rad/s, computed from g and dps with these
  // same constants, so this undoes its conversion (as in imu_acq.cpp).
  Serial.printf("%.3f,%.3f,%.3f,%.2f,%.2f,%.2f,%.2f\n",
                accel.acceleration.x / SENSORS_GRAVITY_STANDARD,
                accel.acceleration.y / SENSORS_GRAVITY_STANDARD,
                accel.acceleration.z / SENSORS_GRAVITY_STANDARD,
                gyro.gyro.x * SENSORS_RADS_TO_DPS,
                gyro.gyro.y * SENSORS_RADS_TO_DPS,
                gyro.gyro.z * SENSORS_RADS_TO_DPS,
                temp.temperature);
}

/**
 * Starts Serial, prints the banner and CSV header, and initializes the IMU.
 * @return None.
 */
void setup() {
  Serial.begin(SERIAL_BAUD);

  // Bounded wait: a monitor that is already open sees the banner, and with no
  // host the board carries on after SERIAL_WAIT_MS. With no host, Serial
  // output is dropped, never blocking.
  const uint32_t wait_start_ms = millis();
  while (!Serial && (millis() - wait_start_ms < SERIAL_WAIT_MS)) {
  }

  Serial.println("# imu_readout " SKETCH_VERSION
                 ": SPI 1 MHz, ODR 416 Hz, accel +-8 g, gyro +-500 dps");
  Serial.println("ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temp_c");

  imu_init();  // On failure s_error is set, and loop() prints it

  s_last_print_ms = millis();
}

/**
 * Once per PRINT_PERIOD_MS, checks WHO_AM_I and prints one sample, or prints
 * the ERR line if imu_init() or an earlier check failed. Repeating the ERR
 * line means a monitor opened late still sees it.
 * @return None.
 */
void loop() {
  if (millis() - s_last_print_ms < PRINT_PERIOD_MS) {
    return;
  }
  // Advance by exactly one period (not to now), so the 1 s cadence does not
  // drift by the time each print takes.
  s_last_print_ms += PRINT_PERIOD_MS;

  // A wire that comes loose after boot often does not look like garbage: a
  // floating MISO tends to read every byte as 0x00 or 0xFF, which looks like
  // a still sensor (~0 g on every axis, ~25 C). Checking WHO_AM_I before each
  // read turns that into an ERR line.
  // Only while the IMU is good, so a failed init never touches it again.
  if (s_error[0] == '\0') {
    const uint8_t id = s_imu.who_am_i();
    if (id != IMU_WHO_AM_I) {
      set_who_am_i_error("IMU lost", id);
    }
  }

  if (s_error[0] != '\0') {
    Serial.print("ERR ");
    Serial.println(s_error);
  } else {
    print_sample();
  }
}
