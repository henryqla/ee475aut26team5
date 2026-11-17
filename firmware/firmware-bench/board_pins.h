/*
 * File:        board_pins.h
 * Authors:     Henry (owner); first draft written with Claude
 * Date:        2026-11-10
 * Version:     0.1.0-bench
 * Change:      Bench build. New BOARD_DEVKITC1_N8R8 entry with the same five
 *              GPIOs as BOARD_ROADQ_PCB, and selected. PCB entry kept as is.
 *              Copied from road_quality_fw 0.2.0-draft (commit 4276b56).
 * Description: GPIO number for every signal the firmware uses. No pin number
 *              appears in any other file. Two pin sets; select exactly one:
 *              - BOARD_DEVKITC1_N8R8 (selected): ESP32-S3-DevKitC-1 with an
 *                ESP32-S3-WROOM-1-N8R8, breadboarded to an Adafruit LSM6DSO32
 *                breakout on SPI (devkit-bench-handoff.md v0.2, "Wiring").
 *              - BOARD_ROADQ_PCB: the team's custom PCB with an
 *                ESP32-S3-WROOM-1-N16R8 and an LSM6DSO32X on SPI
 *                (pin-map.md v0.1).
 *              Both use the same GPIOs, so the bench also tests the PCB pinout.
 *
 *              Any pin change is a PCB or wiring change: Henry approves it.
 *
 *              DevKit: on the board but not used by this firmware, so never
 *              configured here:
 *              - Breakout INT2, and the top-row DO / CS pads (auxiliary
 *                sensor port): not wired.
 *              - GPIO43, 44: USB-to-UART bridge ("UART" connector). Not
 *                Serial, but it still shows ROM boot messages and
 *                configASSERT output.
 *              - GPIO48 or 38 (depends on board revision): RGB LED.
 *              PCB: on the board but not used by this firmware (pin-map.md
 *              v0.1):
 *              - GPIO47: IMU INT2. The IMU drives it; leave it an input.
 *              - GPIO39, 40, 41 (SD D0, CLK, CMD), 38, 2, 42 (SD D1-D3),
 *                48 (card detect): microSD, wired, not used yet.
 *              - GPIO15, 16: LED1, LED2.
 *              - GPIO17, 18: J3 header.
 *              Never usable on either board: GPIO35-37 (octal PSRAM), GPIO19
 *              and 20 (native USB, carries Serial), GPIO0, 3, 45, 46
 *              (strapping).
 */

#ifndef BOARD_PINS_H
#define BOARD_PINS_H

// ============================================================================
// Macros
// ============================================================================

// Select exactly one board.
#define BOARD_DEVKITC1_N8R8
// #define BOARD_ROADQ_PCB

#if defined(BOARD_DEVKITC1_N8R8) && defined(BOARD_ROADQ_PCB)
#error "board_pins.h: select only one board"
#elif defined(BOARD_DEVKITC1_N8R8)

// Same GPIOs as BOARD_ROADQ_PCB. Breakout pad in brackets; DO and CS are the
// bottom-row pads, not the auxiliary pads on top.
#define PIN_SPI_SCK   14  // SPI SCK -> IMU SPC (pad SCL)
#define PIN_SPI_MISO  12  // SPI MISO <- IMU SDO (pad DO, bottom row)
#define PIN_SPI_MOSI  13  // SPI MOSI -> IMU SDI (pad SDA)
#define PIN_IMU_CS    21  // IMU CS, active low (pad CS, bottom row). The library drives it as a GPIO
#define PIN_IMU_INT1  11  // IMU INT1: accel data-ready, active high, push-pull (pad INT1)

#elif defined(BOARD_ROADQ_PCB)

#define PIN_SPI_SCK   14  // SPI SCK -> IMU SPC
#define PIN_SPI_MISO  12  // SPI MISO <- IMU SDO
#define PIN_SPI_MOSI  13  // SPI MOSI -> IMU SDI
#define PIN_IMU_CS    21  // IMU CS, active low. The library drives it as a GPIO
#define PIN_IMU_INT1  11  // IMU INT1: accel data-ready, active high, push-pull

#else
#error "board_pins.h: no board selected"
#endif

#endif  // BOARD_PINS_H
